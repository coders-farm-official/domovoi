"""Media understanding, slice 1 (V078, domovoi/media_understanding/): the
catalogue walker, the runner's windows and caps, the adaptive frame
budget and its benchmark, the switches' defaults, and the routes' tiers.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import voice_activity
from domovoi.db.session import session_scope
from domovoi.media_understanding import budget, catalog, ratelimit, switches
from domovoi.media_understanding import runner as R
from domovoi.tests.conftest import requires_db

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "migrations" / "V078__media_understanding.sql"


@pytest.fixture(autouse=True)
def _quiet_house():
    voice_activity._reset_for_tests()
    yield
    voice_activity._reset_for_tests()


# ─── the frame budget (pure) ──────────────────────────────────────────────

TWO_HOURS = 7200.0
FAST_CPU = budget.Tier("cpu", s_per_frame=5.0)
FAST_GPU = budget.Tier("gpu", s_per_frame=0.8, vram_bytes=12 * 1024 ** 3)


def test_cpu_only_houses_cap_at_120_frames() -> None:
    frames, _t = budget.frame_budget(TWO_HOURS, 900, FAST_CPU, "deep")
    assert frames == 120
    frames, _t = budget.frame_budget(10 * TWO_HOURS, 9000, budget.Tier("cpu", s_per_frame=0.5), "deep")
    assert frames == 120


def test_gpu_houses_go_to_about_600_scaled_by_measured_speed() -> None:
    assert budget.frame_budget(TWO_HOURS, 2000, FAST_GPU, "deep")[0] == 600
    slower = budget.frame_budget(TWO_HOURS, 2000, budget.Tier("gpu", s_per_frame=2.0), "deep")[0]
    assert slower == 300
    # A GPU slower than a CPU's cap would allow never drops below it.
    assert budget.tier_cap(budget.Tier("gpu", s_per_frame=50.0)) == budget.CPU_CAP
    # Little video memory: the model spills, half cap.
    small = budget.Tier("gpu", s_per_frame=0.5, vram_bytes=2 * 1024 ** 3)
    assert budget.frame_budget(TWO_HOURS, 2000, small, "deep")[0] == budget.GPU_LOW_VRAM_CAP


def test_frames_scale_with_length_and_scenes() -> None:
    short = budget.frame_budget(600, 40, FAST_GPU, "deep")[0]
    long_ = budget.frame_budget(TWO_HOURS, 2000, FAST_GPU, "deep")[0]
    assert short < long_
    few_scenes = budget.frame_budget(3600, 5, FAST_CPU, "deep")[0]
    many_scenes = budget.frame_budget(3600, 500, FAST_CPU, "deep")[0]
    assert few_scenes < many_scenes == 60


def test_a_short_clip_uses_far_fewer_but_at_least_the_minimum() -> None:
    assert budget.frame_budget(10, 0, FAST_GPU, "deep")[0] == budget.MIN_FRAMES
    assert budget.frame_budget(10, 1, FAST_CPU, "quick")[0] == budget.MIN_FRAMES
    # Never more than one frame per 2 seconds of a tiny clip.
    assert budget.frame_budget(4, 3, FAST_CPU, "deep")[0] == 2
    assert budget.frame_budget(0, 0, FAST_CPU, "deep")[0] == 1


def test_the_quick_first_pass_takes_fewer_frames_and_less_time_than_deepen() -> None:
    qf, qt = budget.frame_budget(TWO_HOURS, 900, FAST_CPU, "quick")
    df, dt = budget.frame_budget(TWO_HOURS, 900, FAST_CPU, "deep")
    assert qf == 30 and df == 120
    assert qt < dt
    assert budget.choose_pass(None, budget.QUICK_BACKLOG) == "quick"
    assert budget.choose_pass(None, 3) == "deep"
    assert budget.choose_pass("quick", 5) is None       # deepen waits for the first look
    assert budget.choose_pass("quick", 0) == "deep"
    assert budget.choose_pass("deep", 0) is None
    with pytest.raises(ValueError):
        budget.frame_budget(60, 1, FAST_CPU, "medium")


def test_the_admins_manual_cap_overrides_automatic() -> None:
    assert budget.frame_budget(TWO_HOURS, 900, FAST_GPU, "deep", 10)[0] == 10
    frames, time_s = budget.frame_budget(TWO_HOURS, 900, FAST_CPU, "deep", 300)
    assert frames == 300
    assert time_s >= 300 * FAST_CPU.speed / budget.FRAME_SHARE
    # The quick pass still takes its share of a manual cap.
    assert budget.frame_budget(TWO_HOURS, 900, FAST_CPU, "quick", 300)[0] == 75
    # Even a manual cap can't take more than the hard ceiling of time.
    _f, t = budget.frame_budget(TWO_HOURS, 9000, budget.Tier("cpu", s_per_frame=60.0), "deep", 1000)
    assert t == budget.MAX_VIDEO_BUDGET_S


def test_the_time_budget_limits_frames_on_slow_hardware() -> None:
    slow = budget.Tier("cpu", s_per_frame=25.0)
    frames, time_s = budget.frame_budget(TWO_HOURS, 900, slow, "deep")
    assert time_s == budget.TIME_BUDGET_S[("cpu", "deep")]
    assert frames == int(time_s * budget.FRAME_SHARE // 25.0) < budget.CPU_CAP
    assert budget.frame_budget(TWO_HOURS, 900, FAST_GPU, "deep")[1] == budget.TIME_BUDGET_S[("gpu", "deep")]
    assert budget.frame_budget(TWO_HOURS, 900, "cpu", "quick")[1] == budget.TIME_BUDGET_S[("cpu", "quick")]
    assert budget.TRANSCRIPT_MAX_S == 2 * 3600


async def test_the_benchmark_times_real_frames_and_reads_placement_not_names() -> None:
    calls = []
    t = [100.0]

    async def describe(jpeg: bytes) -> str:
        calls.append(len(jpeg))
        t[0] += 4.0
        return "a picture"

    async def on_gpu():
        return {"size": 3_000_000_000, "size_vram": 3_000_000_000}

    bench = await budget.run_benchmark(describe, "vision:2b", placement=on_gpu, clock=lambda: t[0])
    assert len(calls) == 1 + budget.BENCH_FRAMES and all(n > 1000 for n in calls)
    assert bench["s_per_frame"] == 4.0 and bench["frames_per_s"] == 0.25
    assert bench["tier"] == "gpu" and bench["vram_bytes"] == 3_000_000_000
    cpu = await budget.run_benchmark(describe, "vision:2b", placement=None, clock=lambda: t[0])
    assert cpu["tier"] == "cpu" and cpu["vram_bytes"] is None
    assert budget.tier_from_benchmark(bench) == budget.Tier("gpu", 4.0, 3_000_000_000)


# ─── the switches (pure) ──────────────────────────────────────────────────

def test_switches_validate_and_a_missing_row_is_all_off() -> None:
    off = switches.normalize(None)
    assert off["enabled"] is False and off["faces"] is False and off["places"] is False
    cur = switches.normalize({"enabled": True})
    new = switches.apply_changes(cur, {"frame_cap_mode": "manual", "frame_cap": 50})
    assert switches.admin_cap(new) == 50 and new["set_by"] == "admin"
    for bad in ({"enabled": "yes"}, {"frame_cap": 2}, {"frame_cap": True}, {"nope": 1},
                {"frame_cap_mode": "manual", "frame_cap": None}):
        with pytest.raises(switches.InvalidSwitches):
            switches.apply_changes(cur, bad)


# ─── the read-rate bucket (pure) ──────────────────────────────────────────

async def test_reads_go_through_a_20_mb_per_second_bucket(tmp_path) -> None:
    now = [0.0]

    async def sleep(s: float) -> None:
        now[0] += s

    b = ratelimit.TokenBucket(clock=lambda: now[0], sleep=sleep)
    assert b.rate == 20 * 1024 * 1024
    f = tmp_path / "big.bin"
    f.write_bytes(os.urandom(1024) * (60 * 1024))  # 60 MB
    data = await ratelimit.read_range(f, 0, f.stat().st_size, bucket=b)
    assert len(data) == 60 * 1024 * 1024
    # 60 MB at 20 MB/s, less the 1 MB burst: about three seconds.
    assert 2.9 <= now[0] <= 3.1
    # A fingerprint reads two ends of at most 1 MB, not the file.
    before = b.taken
    fp = await ratelimit.fingerprint(f, bucket=b)
    assert b.taken - before == 2 * 1024 * 1024
    assert fp.startswith(f"{60 * 1024 * 1024}:")


# ─── the windows (pure) ───────────────────────────────────────────────────

def _runner(**kw) -> R.Runner:
    kw.setdefault("chat", _NoChat())
    kw.setdefault("brains", _Brains())
    kw.setdefault("memory", lambda: (64 * 1024 ** 3, 64 * 1024 ** 3))
    kw.setdefault("model_bytes", _model_bytes)
    kw.setdefault("placement", _no_placement)
    kw.setdefault("vision_model", lambda: "vision:2b")
    kw.setdefault("quiet_hours", lambda: ("22:00", "07:00"))
    kw.setdefault("poll_sec", 0.01)
    kw.setdefault("quiet_sec", 0.0)
    kw.setdefault("memory_poll_s", 0.01)
    return R.Runner(**kw)


class _NoChat:
    async def run_next(self):
        return None


class _Brains:
    def __init__(self) -> None:
        self.released = 0

    async def describe(self, jpeg: bytes) -> str:
        return "x"

    async def release_vision(self) -> None:
        self.released += 1


async def _model_bytes(model: str) -> int:
    return 2 * 1024 ** 3


async def _no_placement():
    return None


def test_the_backlog_runs_in_the_night_window_and_run_now_ignores_it() -> None:
    at = {"t": datetime(2026, 11, 22, 23, 30)}
    r = _runner(now=lambda: at["t"])
    assert r.mode() == "night"
    at["t"] = datetime(2026, 11, 22, 6, 59)
    assert r.mode() == "night"
    at["t"] = datetime(2026, 11, 22, 7, 0)
    assert r.mode() == "day"          # a quiet daytime house: trickle only
    r.sw["run_now"] = True
    assert r.mode() == "night"


async def test_the_daytime_trickle_waits_ten_quiet_minutes_and_rests_one_to_one() -> None:
    clock = [1000.0]
    r = _runner(now=lambda: datetime(2026, 11, 22, 12, 0), clock=lambda: clock[0])
    assert r.mode() == "day"
    async with voice_activity.turn("satellite"):
        assert r.mode() is None
    # A turn just ended: not before ten quiet minutes.
    assert r.mode() is None
    voice_activity._last_end -= R.TRICKLE_IDLE_S + 1
    assert r.mode() == "day"
    r.rest_until = clock[0] + 30
    assert r.mode() is None
    clock[0] += 31
    assert r.mode() == "day"


# ─── the database ─────────────────────────────────────────────────────────

#: The Files libraries besides Pictures and Videos a test hands the
#: catalogue (``catalog.other_libraries``), cleared by ``house``.
OTHER_LIBS: list[catalog.Library] = []


@pytest.fixture
async def house(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "WORK_ROOT", tmp_path / "work")
    async with session_scope() as s:
        await s.execute(text("TRUNCATE media_items, media_libraries, media_excluded, face_groups, "
                             "face_denylist RESTART IDENTITY CASCADE"))
        await s.execute(text("UPDATE media_runner_state SET phase = 'idle', waiting_for_voice = false, "
                             "current_item = NULL, rescan_requested = false, benchmark = NULL"))
        old = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                               {"k": switches.KEY})).scalar()
        await switches.write(s, {**switches.DEFAULTS, "enabled": True})
    for name in list(R._ANALYSERS):
        if name != "fingerprint":
            R.unregister(name)
    pics = tmp_path / "Pictures"
    pics.mkdir()
    lib = catalog.Library("core:pictures", pics, frozenset({"image", "video"}), "Pictures")
    monkeypatch.setattr(catalog, "core_libraries", lambda settings=None: [lib])
    # The other Files libraries (plugins', removable drives') are a test's
    # own choice (OTHER_LIBS): never this machine's real drives.
    OTHER_LIBS.clear()

    async def _others(s=None):
        return list(OTHER_LIBS)

    monkeypatch.setattr(catalog, "other_libraries", _others)
    monkeypatch.setattr(catalog, "_other_cache", None)
    yield lib
    OTHER_LIBS.clear()
    for name in list(R._ANALYSERS):
        if name != "fingerprint":
            R.unregister(name)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE media_items, media_libraries, media_excluded RESTART IDENTITY CASCADE"))
        if old is not None:
            await switches.write(s, old)


def _file(lib: catalog.Library, rel: str, data: bytes = b"x" * 100, mtime: float | None = None) -> Path:
    p = lib.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


async def _sync(lib: catalog.Library) -> dict[str, int]:
    async with session_scope() as s:
        return await catalog.sync_library(s, lib)


async def _items() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text("SELECT * FROM media_items ORDER BY rel_path"))).mappings().all()
    return {r["rel_path"]: dict(r) for r in rows}


async def _add_results(item_id: int) -> None:
    async with session_scope() as s:
        await s.execute(text("INSERT INTO media_frames (item_id, idx, caption) VALUES (:i, 0, 'a dog')"),
                        {"i": item_id})
        await s.execute(text("INSERT INTO media_item_stages (item_id, analyser, state) VALUES (:i, 'x', 'done')"),
                        {"i": item_id})


async def _count(table: str) -> int:
    async with session_scope() as s:
        return int((await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar())


@requires_db
async def test_the_walker_adds_changes_and_deletes_with_the_results(house) -> None:
    lib = house
    _file(lib, "a.jpg")
    _file(lib, "trip/b.mp4")
    _file(lib, "notes.txt")
    _file(lib, ".hidden/c.jpg")
    st = await _sync(lib)
    assert st["added"] == 2
    items = await _items()
    assert set(items) == {"a.jpg", "trip/b.mp4"}
    assert items["a.jpg"]["kind"] == "image" and items["trip/b.mp4"]["kind"] == "video"
    # The first walk is the backlog; later finds are new arrivals.
    assert not items["a.jpg"]["new_arrival"]
    _file(lib, "c.png")
    await _sync(lib)
    assert (await _items())["c.png"]["new_arrival"]

    # Changed: re-queued, old results gone.
    a = items["a.jpg"]["id"]
    await _add_results(a)
    async with session_scope() as s:
        await s.execute(text("UPDATE media_items SET state = 'done' WHERE id = :i"), {"i": a})
    _file(lib, "a.jpg", b"y" * 300)
    assert (await _sync(lib))["changed"] == 1
    assert (await _items())["a.jpg"]["state"] == "pending"
    assert await _count("media_frames") == 0 and await _count("media_item_stages") == 0

    # Deleted: missing first (results kept), then after the grace the row
    # and every result go with the file.
    await _add_results(a)
    (lib.root / "a.jpg").unlink()
    st = await _sync(lib)
    assert st["deleted"] == 0 and st["missing"] == 1
    assert (await _items())["a.jpg"]["missing_since"] is not None
    await _past_grace()
    assert (await _sync(lib))["deleted"] == 1
    assert "a.jpg" not in await _items()
    assert await _count("media_frames") == 0 and await _count("media_item_stages") == 0


async def _past_grace() -> None:
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE media_items SET missed_walks = :w, missing_since = now() - interval '30 days' "
            "WHERE missing_since IS NOT NULL"), {"w": catalog.MISSING_GRACE_WALKS})


@requires_db
async def test_an_empty_mount_an_unreadable_folder_or_the_cap_never_deletes(house) -> None:
    """M10 (security review 2026-11-23)."""
    lib = house
    for i in range(6):
        _file(lib, f"trip/{i}.jpg", os.urandom(100 + i))
    _file(lib, "top.jpg")
    await _sync(lib)
    ids = {k: v["id"] for k, v in (await _items()).items()}
    await _add_results(ids["trip/0.jpg"])
    await _past_grace()
    # An unreadable folder: the walk is incomplete, nothing counts as missing.
    st = await _sync_walk(lib, catalog.Walk({"top.jpg": _stat(lib, "top.jpg")}, complete=False, unreadable=1))
    assert st["deleted"] == 0 and st["missing"] == 0 and st["held"] == 6
    # The cap: the same.
    st = await _sync_walk(lib, catalog.Walk({"trip/0.jpg": _stat(lib, "trip/0.jpg")}, complete=False))
    assert st["deleted"] == 0 and st["missing"] == 0
    # The root can't be listed: not present, nothing touched.
    st = await _sync_walk(lib, catalog.Walk({}, complete=False, root_ok=False, unreadable=1))
    assert st["deleted"] == 0
    # An empty mount point (the root is there, nothing in it): not present.
    for f in list(lib.root.rglob("*.jpg")):
        f.unlink()
    st = await _sync(lib)
    assert st["deleted"] == 0 and st["held"] == 7
    assert {k: v["id"] for k, v in (await _items()).items()} == ids
    assert all(v["missing_since"] is None for v in (await _items()).values())
    assert await _count("media_frames") == 1
    async with session_scope() as s:
        assert (await s.execute(text(
            "SELECT present FROM media_libraries WHERE library_id = 'core:pictures'"))).scalar() is False
    # The real walker flags an unreadable folder and the cap.
    for i in range(3):
        _file(lib, f"x/{i}.jpg")
    w = catalog.walk_full(lib, limit=2)
    assert not w.complete and len(w.found) == 2
    assert catalog.walk_full(lib).complete


@requires_db
async def test_a_big_loss_in_one_walk_waits_longer(house) -> None:
    lib = house
    for i in range(30):
        _file(lib, f"{i}.jpg", os.urandom(50 + i))
    await _sync(lib)
    for i in range(25):
        (lib.root / f"{i}.jpg").unlink()
    assert (await _sync(lib))["missing"] == 25
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE media_items SET missed_walks = :w, missing_since = now() - interval '2 days' "
            "WHERE missing_since IS NOT NULL"), {"w": catalog.MISSING_GRACE_WALKS})
    assert (await _sync(lib))["deleted"] == 0                 # 2 days < the mass-loss grace
    await _past_grace()
    assert (await _sync(lib))["deleted"] == 25


@requires_db
async def test_a_touched_file_keeps_its_results_and_a_move_across_libraries_is_matched(house, tmp_path) -> None:
    lib = house
    p = _file(lib, "a.jpg", os.urandom(4000))
    await _sync(lib)
    item = (await _items())["a.jpg"]
    async with session_scope() as s:
        await s.execute(text("UPDATE media_items SET fingerprint = :f, state = 'done' WHERE id = :i"),
                        {"f": await ratelimit.fingerprint(p), "i": item["id"]})
    await _add_results(item["id"])
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 100))
    st = await _sync(lib)
    assert st["changed"] == 0 and await _count("media_frames") == 1
    assert (await _items())["a.jpg"]["state"] == "done"
    # Moved to another library: the first walk marks it missing, the other
    # library's walk finds it by fingerprint and the row moves, results and all.
    other_root = tmp_path / "drive"
    other_root.mkdir()
    other = catalog.Library("drive:usb", other_root, lib.kinds)
    _file(lib, "keep.jpg")
    await _sync(lib)
    p.rename(other_root / "a.jpg")
    assert (await _sync(lib))["missing"] == 1
    st = await _sync(other)
    assert st["moved"] == 1 and st["added"] == 0
    async with session_scope() as s:
        row = (await s.execute(text("SELECT library_id, rel_path, missing_since FROM media_items WHERE id = :i"),
                               {"i": item["id"]})).mappings().first()
    assert row["library_id"] == "drive:usb" and row["missing_since"] is None
    assert await _count("media_frames") == 1


def _stat(lib, rel: str) -> tuple[int, float, str]:
    st = (lib.root / rel).stat()
    return (int(st.st_size), float(st.st_mtime), "image")


async def _sync_walk(lib, w) -> dict[str, int]:
    async with session_scope() as s:
        return await catalog.sync_library(s, lib, found=w)


@requires_db
async def test_a_moved_file_keeps_its_results_by_fingerprint(house) -> None:
    lib = house
    p = _file(lib, "old/name.jpg", os.urandom(5000))
    await _sync(lib)
    item = (await _items())["old/name.jpg"]
    async with session_scope() as s:
        await s.execute(text("UPDATE media_items SET fingerprint = :f WHERE id = :i"),
                        {"f": await ratelimit.fingerprint(p), "i": item["id"]})
    await _add_results(item["id"])
    (lib.root / "new").mkdir()
    p.rename(lib.root / "new" / "renamed.jpg")
    st = await _sync(lib)
    assert st["moved"] == 1 and st["deleted"] == 0
    moved = (await _items())["new/renamed.jpg"]
    assert moved["id"] == item["id"]
    assert await _count("media_frames") == 1


@requires_db
async def test_an_unplugged_library_keeps_its_rows(house, tmp_path) -> None:
    lib = house
    _file(lib, "a.jpg")
    await _sync(lib)
    gone = catalog.Library("core:pictures", tmp_path / "not-mounted", lib.kinds)
    await _sync(gone)
    assert "a.jpg" in await _items()
    async with session_scope() as s:
        assert (await s.execute(text("SELECT present FROM media_libraries"))).scalar() is False


@requires_db
async def test_excluded_folders_are_never_walked_and_their_results_go(house) -> None:
    lib = house
    _file(lib, "Private/x.jpg")
    _file(lib, "Private/deep/y.jpg")
    _file(lib, "Privateer.jpg")
    _file(lib, "keep.jpg")
    await _sync(lib)
    assert len(await _items()) == 4
    await _add_results((await _items())["Private/x.jpg"]["id"])
    async with session_scope() as s:
        await s.execute(text("INSERT INTO media_excluded (library_id, prefix) VALUES ('core:pictures', 'Private')"))
    st = await _sync(lib)
    assert st["excluded"] == 2
    assert set(await _items()) == {"Privateer.jpg", "keep.jpg"}
    assert await _count("media_frames") == 0
    assert set(catalog.walk(lib, ["Private"])) == {"Privateer.jpg", "keep.jpg"}
    assert catalog.walk(lib, [""]) == {}


@requires_db
async def test_the_catalogue_walks_every_files_library_videos_only_from_core_ones(house, tmp_path) -> None:
    usb = tmp_path / "usb"
    music = tmp_path / "Music"
    inside = house.root / "Plugin"            # a root inside Pictures: left out
    for d in (usb, music, inside):
        d.mkdir()
    OTHER_LIBS.extend([
        catalog.Library("removable:abc", usb, frozenset({"image", "video"}), "USB (E:)"),
        catalog.Library("core:music", music, frozenset({"video"}), "Music"),
        catalog.Library("plugin:x:pics", inside, frozenset({"image", "video"}), "X · Pictures"),
    ])
    libs = await catalog.libraries(fresh=True)
    assert [lib.id for lib in libs] == ["core:pictures", "removable:abc", "core:music"]
    (usb / "a.jpg").write_bytes(b"x" * 10)
    (usb / "b.mp4").write_bytes(b"x" * 10)
    (music / "cover.jpg").write_bytes(b"x" * 10)
    (music / "live.mp4").write_bytes(b"x" * 10)
    out = await catalog.Catalog().tick(session_scope, force=True)
    assert set(out) == {"core:pictures", "removable:abc", "core:music"}
    async with session_scope() as s:
        rows = (await s.execute(text("SELECT library_id, rel_path FROM media_items ORDER BY 1, 2"))).all()
    assert [tuple(r) for r in rows] == [("core:music", "live.mp4"), ("removable:abc", "a.jpg"),
                                        ("removable:abc", "b.mp4")]
    # The runner finds a file in any of them.
    item = R.Item.from_row({**(await _items())["a.jpg"]})
    assert (await _runner()._with_path(item)).path == usb / "a.jpg"


@requires_db
async def test_any_library_can_be_excluded_with_a_confirm_and_its_results_go(house, tmp_path, monkeypatch) -> None:
    import numpy as np

    from domovoi.media_understanding import faces as F
    from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client

    usb = tmp_path / "usb"
    (usb / "Trip").mkdir(parents=True)
    OTHER_LIBS.append(catalog.Library("removable:abc", usb, frozenset({"image", "video"}), "USB (E:)"))
    for rel in ("Trip/a.jpg", "Trip/b.jpg", "c.jpg"):
        (usb / rel).write_bytes(b"x" * 10)
    _file(house, "d.jpg")
    await catalog.Catalog().tick(session_scope, force=True)
    items = await _items()
    async with session_scope() as s:
        await s.execute(text("UPDATE media_items SET search_doc = to_tsvector('english', 'beach'), "
                             "place_name = 'Portland' WHERE library_id = 'removable:abc'"))
        gid = (await s.execute(text("INSERT INTO face_groups (centroid) VALUES (NULL) RETURNING id"))).scalar()
        for name in ("Trip/a.jpg", "d.jpg"):
            emb = F.to_bytes(F.unit(np.ones(128, dtype=np.float32) * (1 if name == "d.jpg" else -1)))
            await s.execute(text("INSERT INTO media_faces (item_id, box, embedding, model, group_id) "
                                 "VALUES (:i, '{0,0,1,1}', :e, 'sface', :g)"),
                            {"i": items[name]["id"], "e": emb, "g": gid})
    await _add_results(items["Trip/a.jpg"]["id"])
    install_fake_db(monkeypatch, admin=True, sessions={"adm"})
    adm = bearer("adm")
    async with web_client() as c:
        h = {**c.headers, **adm}
        body = (await c.get("/api/media-understanding", headers=h)).json()
        assert {"id": "removable:abc", "label": "USB (E:)", "present": True,
                "kinds": ["image", "video"]} in body["libraries"]
        # A folder on the drive: asked first, then its results go, faces too
        # (the group is worked out again from the face left).
        r = await c.post("/api/media-understanding/excluded", headers=h,
                         json={"library_id": "removable:abc", "prefix": "Trip"})
        assert r.status_code == 409 and r.json()["detail"]["files"] == 2
        r = await c.post("/api/media-understanding/excluded", headers=h,
                         json={"library_id": "removable:abc", "prefix": "Trip", "confirm_delete": True})
        assert r.status_code == 200 and r.json()["deleted"] == 2
        assert set(await _items()) == {"c.jpg", "d.jpg"}
        assert await _count("media_frames") == 0 and await _count("media_faces") == 1
        async with session_scope() as s:
            centroid = (await s.execute(text("SELECT centroid FROM face_groups WHERE id = :g"), {"g": gid})).scalar()
        assert F.from_bytes(centroid)[0] > 0                         # d.jpg's face alone now
        # The walker never brings it back.
        await catalog.Catalog().tick(session_scope, force=True)
        assert set(await _items()) == {"c.jpg", "d.jpg"}
        # The drive unplugged: its rows stay, and it can still be excluded whole.
        OTHER_LIBS.clear()
        catalog._other_cache = None
        body = (await c.get("/api/media-understanding", headers=h)).json()
        assert {"id": "removable:abc", "label": "removable:abc", "present": False, "kinds": []} in body["libraries"]
        r = await c.post("/api/media-understanding/excluded", headers=h,
                         json={"library_id": "removable:abc", "prefix": "", "confirm_delete": True})
        assert r.status_code == 200 and r.json()["deleted"] == 1
        # Nothing to delete: no confirmation needed. An unknown library: 404.
        r = await c.post("/api/media-understanding/excluded", headers=h,
                         json={"library_id": "core:pictures", "prefix": "Empty"})
        assert r.status_code == 200 and r.json()["deleted"] == 0
        r = await c.post("/api/media-understanding/excluded", headers=h,
                         json={"library_id": "plugin:nope:x", "prefix": ""})
        assert r.status_code == 404
        # Included again, even with its drive away.
        r = await c.delete("/api/media-understanding/excluded?library_id=removable:abc&prefix=Trip", headers=h)
        assert r.status_code == 200


@requires_db
async def test_files_deleted_through_files_lose_their_results_at_once(house) -> None:
    from web.backend.api import media_understanding as api

    lib = house
    _file(lib, "trip/a.jpg")
    _file(lib, "trip/b.jpg")
    _file(lib, "c.jpg")
    await _sync(lib)
    await api.forget_deleted("core:pictures", ["trip"])
    assert set(await _items()) == {"c.jpg"}


# ── the runner ──────────────────────────────────────────────────────────

class _Analyser:
    kinds = frozenset({"image", "video"})
    order = 10
    deepens = False

    def __init__(self, name: str, work) -> None:
        self.name = name
        self.work = work
        self.runs = 0

    def wanted(self, item, sw) -> bool:
        return True

    async def run(self, item, ctx) -> None:
        self.runs += 1
        await self.work(item, ctx)


async def _seed(lib, rels, *, new_arrival: bool = False) -> None:
    for rel in rels:
        _file(lib, rel, os.urandom(2000))
    await _sync(lib)
    if new_arrival:
        async with session_scope() as s:
            await s.execute(text("UPDATE media_items SET new_arrival = true"))


@requires_db
async def test_the_runner_fingerprints_and_finishes_items_in_the_night(house) -> None:
    await _seed(house, ["a.jpg", "b.mp4"])
    r = _runner(now=lambda: datetime(2026, 11, 22, 23, 0))
    assert await r.step() and await r.step()
    assert not await r.step()
    items = await _items()
    assert all(i["state"] == "done" and i["fingerprint"] for i in items.values())
    assert items["b.mp4"]["frame_pass"] == "deep"


@requires_db
async def test_chat_videos_go_first(house) -> None:
    await _seed(house, ["a.jpg"])

    class Chat:
        def __init__(self) -> None:
            self.left = 2

        async def run_next(self):
            if self.left:
                self.left -= 1
                return "job"
            return None

    r = _runner(chat=Chat(), now=lambda: datetime(2026, 11, 22, 23, 0))
    await r.step()
    await r.step()
    assert (await _items())["a.jpg"]["state"] == "pending"
    await r.step()
    assert (await _items())["a.jpg"]["state"] == "done"


@requires_db
async def test_daytime_takes_only_new_arrivals(house) -> None:
    await _seed(house, ["old.jpg"])
    r = _runner(now=lambda: datetime(2026, 11, 22, 12, 0))
    assert not await r.step()               # the backlog waits for the night
    _file(house, "new.jpg", os.urandom(100))
    await _sync(house)                      # a later walk: a new arrival
    assert await r.step()
    items = await _items()
    assert items["new.jpg"]["state"] == "done" and items["old.jpg"]["state"] == "pending"
    assert r.rest_until > 0                 # the 1:1 duty cycle


@requires_db
async def test_off_and_paused_take_no_work_and_run_now_clears_when_done(house) -> None:
    await _seed(house, ["a.jpg"])
    r = _runner(now=lambda: datetime(2026, 11, 22, 12, 0))
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": False})
    assert not await r.step()
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "paused": True})
    assert not await r.step()
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "run_now": True})
    assert await r.step()                   # daytime, but Run now
    assert not await r.step()
    async with session_scope() as s:
        assert (await switches.read(s))["run_now"] is False


@requires_db
async def test_a_voice_turn_cancels_the_model_step_which_runs_again_after(house) -> None:
    await _seed(house, ["a.jpg"])
    started = []

    async def work(item, ctx):
        async def call():
            started.append(1)
            await asyncio.sleep(0.3)
            return "a caption"

        said = await ctx.model_step(call, vision=True)
        await ctx.save({"caption": said})

    a = _Analyser("caption", work)
    R.register(a)
    r = _runner(now=lambda: datetime(2026, 11, 22, 23, 0))

    async def a_turn():
        while not started:
            await asyncio.sleep(0.01)
        async with voice_activity.turn("satellite"):
            await asyncio.sleep(0.1)

    await asyncio.gather(r.step(), a_turn())
    assert r.voice_cancels == 1 and len(started) == 2
    async with session_scope() as s:
        row = (await s.execute(text("SELECT state, progress FROM media_item_stages WHERE analyser = 'caption'"))).first()
    assert row[0] == "done" and row[1] == {"caption": "a caption"}


@requires_db
async def test_the_memory_gate_waits_for_room_for_the_vision_model(house) -> None:
    await _seed(house, ["a.jpg"])
    mem = {"avail": 1 * 1024 ** 3}
    calls = []

    async def work(item, ctx):
        await ctx.model_step(lambda: _note(calls), vision=True)

    R.register(_Analyser("caption", work))
    r = _runner(now=lambda: datetime(2026, 11, 22, 23, 0),
                memory=lambda: (mem["avail"], 16 * 1024 ** 3))

    async def free_up():
        while not r.memory_waits:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert not calls                    # held while memory was short
        mem["avail"] = 8 * 1024 ** 3
    assert await r.vision_need() == 2 * 1024 ** 3 + R.MEMORY_HEADROOM
    await asyncio.gather(r.step(), free_up())
    assert calls and r.memory_waits == 1
    # 16 GB is the floor tier: the vision model is let go after the batch.
    assert not await r.step()
    assert r.brains.released == 1


async def _note(calls):
    calls.append(1)
    return "ok"


@requires_db
async def test_work_resumes_from_saved_progress_after_a_restart(house) -> None:
    await _seed(house, ["a.mp4"])
    seen = []

    async def work(item, ctx):
        seen.append(dict(ctx.progress))
        done = ctx.progress.get("pieces", 0)
        for n in range(done, 4):
            await ctx.save({"pieces": n + 1})
            if n == 1 and len(seen) == 1:
                raise asyncio.CancelledError()   # the core stopped mid-item

    R.register(_Analyser("transcript", work))
    r = _runner(now=lambda: datetime(2026, 11, 22, 23, 0))
    with pytest.raises(asyncio.CancelledError):
        await r.step()
    assert (await _items())["a.mp4"]["state"] == "running"
    r2 = _runner(now=lambda: datetime(2026, 11, 22, 23, 0))
    assert await r2.step()
    assert seen == [{}, {"pieces": 2}]
    assert (await _items())["a.mp4"]["state"] == "done"


@requires_db
async def test_the_per_video_time_cap_ends_a_long_video_with_what_it_saved(house) -> None:
    await _seed(house, ["long.mp4"])
    clock = [0.0]

    async def work(item, ctx):
        frames, time_s = await ctx.frame_budget(TWO_HOURS, 900)
        assert ctx.time_budget_s == time_s
        for n in range(frames * 10):
            async with ctx.charge():
                clock[0] += 60.0                   # a slow minute per frame
            await ctx.save({"frames": n + 1})

    R.register(_Analyser("frames", work))
    async with session_scope() as s:
        await budget.save_benchmark(s, {"version": budget.BENCH_VERSION, "model": "vision:2b",
                                        "s_per_frame": 20.0, "tier": "cpu", "vram_bytes": None})
    r = _runner(now=lambda: datetime(2026, 11, 22, 23, 0), clock=lambda: clock[0])
    assert await r.step()
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT state, capped, spent_s, progress FROM media_item_stages WHERE analyser = 'frames'"))).mappings().first()
    assert row["state"] == "done" and row["capped"] is True
    assert row["spent_s"] == budget.TIME_BUDGET_S[("cpu", "deep")]
    assert row["progress"]["frames"] == budget.TIME_BUDGET_S[("cpu", "deep")] // 60
    assert (await _items())["long.mp4"]["state"] == "done"


@requires_db
async def test_three_failures_and_the_item_fails_with_a_code(house) -> None:
    await _seed(house, ["a.jpg"])

    async def work(item, ctx):
        raise RuntimeError("the model fell over")

    R.register(_Analyser("caption", work))
    r = _runner(now=lambda: datetime(2026, 11, 22, 23, 0))
    for _ in range(3):
        assert await r.step()
    item = (await _items())["a.jpg"]
    assert item["state"] == "failed" and item["error_code"] == R.CODE_FAILED
    assert not await r.step()


@requires_db
async def test_the_benchmark_is_stored_reused_and_redone_after_a_model_change(house) -> None:
    model = {"name": "vision:2b"}
    brains = _Brains()
    r = _runner(brains=brains, vision_model=lambda: model["name"])
    counted = []
    real = brains.describe

    async def describe(jpeg):
        counted.append(1)
        return await real(jpeg)

    brains.describe = describe
    t1 = await r.tier()
    assert t1.kind == "cpu" and t1.s_per_frame
    n = len(counted)
    assert n == 1 + budget.BENCH_FRAMES
    await r.tier()
    assert len(counted) == n                          # reused, not measured again
    async with session_scope() as s:
        assert (await budget.stored_benchmark(s))["model"] == "vision:2b"
    model["name"] = "vision:7b"
    await r.tier()
    assert len(counted) == 2 * n                      # a new model: measured again
    async with session_scope() as s:
        assert (await budget.stored_benchmark(s))["model"] == "vision:7b"


# ── the defaults: new installs vs upgraded houses (V078's seed) ─────────

def _seed_statement() -> str:
    sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))
    return next(st.strip() for st in sql.split(";") if "household_settings" in st and "INSERT" in st)


async def _run_seed() -> dict[str, Any]:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM household_settings WHERE key = :k"), {"k": switches.KEY})
        await s.execute(text(_seed_statement()))
        return (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                                {"k": switches.KEY})).scalar()


@requires_db
async def test_a_new_install_starts_on_and_an_upgraded_house_off(house, db_session) -> None:
    # db_session emptied the house: a new install.
    new = await _run_seed()
    assert new["enabled"] is True and new["set_by"] == "install"
    assert new["faces"] is False and new["places"] is False and new["frame_cap_mode"] == "auto"
    async with session_scope() as s:
        await s.execute(text("INSERT INTO people (name) VALUES ('Alex')"))
    old = await _run_seed()
    assert old["enabled"] is False and old["set_by"] == "upgrade"
    assert old["faces"] is False and old["places"] is False
    # The seed never overwrites a choice.
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "set_by": "admin"})
        await s.execute(text(_seed_statement()))
        assert (await switches.read(s))["enabled"] is True


# ── the routes and their tiers ───────────────────────────────────────────

ROUTES = [
    ("GET", "/api/media-understanding", None, "household"),
    ("POST", "/api/media-understanding/rescan", {}, "admin"),
    ("PUT", "/api/media-understanding/settings", {"places": True}, "admin"),
    ("POST", "/api/media-understanding/pause", {"paused": True}, "admin"),
    ("POST", "/api/media-understanding/run-now", {}, "admin"),
    ("POST", "/api/media-understanding/excluded", {"library_id": "core:pictures", "prefix": "Work"}, "admin"),
    ("DELETE", "/api/media-understanding/excluded?library_id=core:pictures&prefix=Work", None, "admin"),
]


@requires_db
async def test_the_routes_tier_matrix(house, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_client

    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    creds = {"none": {}, "household": {HEADER: "household"}, "admin": bearer("adm")}
    async with web_client() as c:
        for who, headers in creds.items():
            for method, path, body, tier in ROUTES:
                kw: dict[str, Any] = {"headers": {**c.headers, **headers}}
                if body is not None:
                    kw["json"] = body
                r = await c.request(method, path, **kw)
                allowed = who == "admin" or (who == "household" and tier == "household")
                if allowed:
                    assert r.status_code == 200, (who, method, path, r.status_code, r.text)
                else:
                    assert r.status_code in (401, 403), (who, method, path, r.status_code)


@requires_db
async def test_the_state_route_and_the_admin_writes(house, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client

    install_fake_db(monkeypatch, admin=True, sessions={"adm"})
    _file(house, "Work/a.jpg")
    _file(house, "b.jpg")
    await _sync(house)
    adm = bearer("adm")
    async with web_client() as c:
        body = (await c.get("/api/media-understanding", headers={**c.headers, **adm})).json()
        assert body["progress"]["photos"] == {"total": 2, "done": 0, "left": 2, "failed": 0}
        assert body["settings"]["enabled"] is True and body["settings"]["faces"] is False
        assert body["libraries"][0]["id"] == "core:pictures"
        # A folder with results: only with the confirmation (409 says how many).
        r = await c.post("/api/media-understanding/excluded", headers={**c.headers, **adm},
                         json={"library_id": "core:pictures", "prefix": "/Work/"})
        assert r.status_code == 409 and r.json()["detail"]["files"] == 1
        assert len(await _items()) == 2
        r = await c.post("/api/media-understanding/excluded", headers={**c.headers, **adm},
                         json={"library_id": "core:pictures", "prefix": "/Work/", "confirm_delete": True})
        assert r.json() == {"library_id": "core:pictures", "prefix": "Work", "deleted": 1}
        for bad in ({"library_id": "core:music", "prefix": ""},
                    {"library_id": "core:pictures", "prefix": "../etc"}):
            r = await c.post("/api/media-understanding/excluded", headers={**c.headers, **adm}, json=bad)
            assert r.status_code in (404, 422)
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm},
                        json={"frame_cap_mode": "manual", "frame_cap": 2})
        assert r.status_code == 422
        # Faces off with faces saved: only with the confirmation, and they all go.
        await c.put("/api/media-understanding/settings", headers={**c.headers, **adm}, json={"faces": True})
        item = (await _items())["b.jpg"]["id"]
        async with session_scope() as s:
            await s.execute(text("INSERT INTO media_faces (item_id, box, embedding, model) "
                                 "VALUES (:i, '{0,0,1,1}', '\\x00', 'sface')"), {"i": item})
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm}, json={"faces": False})
        assert r.status_code == 409
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm},
                        json={"faces": False, "confirm_delete_faces": True})
        assert r.status_code == 200 and await _count("media_faces") == 0


async def test_library_reads_run_off_the_event_loop(tmp_path, monkeypatch) -> None:
    """L50: the open and every read of a library file run in a worker
    thread, so a sleeping disk stalls the item, not voice."""
    import asyncio as _asyncio
    import threading

    p = tmp_path / "a.jpg"
    p.write_bytes(b"abcdef" * 1000)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = _asyncio.to_thread

    async def spy(fn, *a, **k):
        def wrapped():
            seen.append(threading.get_ident())
            return fn(*a, **k)
        return await real(wrapped)

    monkeypatch.setattr(ratelimit.asyncio, "to_thread", spy)
    data = await ratelimit.read_range(p, 6, 12)
    assert bytes(data) == b"abcdefabcdef"
    assert seen and loop_thread not in seen
    assert len(seen) >= 3                                          # open, seek, read (and close)


def test_the_walker_skips_names_that_are_not_utf8(tmp_path, monkeypatch) -> None:
    """L55: a file name with undecodable bytes (a surrogate on Linux) is
    skipped instead of stopping the library's catalogue."""
    lib = catalog.Library("core:pictures", tmp_path, frozenset({"image"}))
    (tmp_path / "ok.jpg").write_bytes(b"x")

    class _E:
        def __init__(self, name):
            self.name = name
            self.path = str(tmp_path / "ok.jpg")

        def is_symlink(self):
            return False

        def is_dir(self, follow_symlinks=False):
            return False

        def stat(self, follow_symlinks=False):
            return (tmp_path / "ok.jpg").stat()

    monkeypatch.setattr(catalog.os, "scandir", lambda folder: [_E("ok.jpg"), _E("bad\udcff.jpg")])
    assert set(catalog.walk(lib)) == {"ok.jpg"}
