"""Rebuilds of the music player image by Domovoi's update unit, and the
container guard (docs/CONTAINER_GUARD.md, "The music players").

2026-10-25, on the Windows install (main fa02bcb2): an update ran, and
about half an hour later the first music in a newly set-up room made the
core create ``domovoi-mpd-dining-room`` from ``domovoi-mpd:latest``. The
guard opened incident #5, "tampering · different image": it expected the
image it had recorded (``sha256:03c3a6ac…``) and found the one the tag
named by then (``sha256:b05fcdd8…``). Nothing recorded the rebuilt image
as Domovoi's own, so every room's player made from it afterwards was
reported, and the freeze held updates, plugin installs, room upgrades and
media preparation.

The fix, tested here:

* the update unit's rebuild-mpd step records the image it built in a
  root-owned file (``/var/lib/domovoi-update/mpd-image.json``, written
  before the core starts: scripts/linux/tests/test-apply-update.sh), the
  way provision.sh records its builds;
* the guard takes a vouched image on at any check, not only at the core's
  start, so a room created from the new tag before anything restarts is
  not reported; an image nobody vouched for (a ``docker build`` by hand)
  still is;
* an admin who clears an incident about the music player image keeps that
  image: the next room made from it isn't reported again;
* the incident says when the evidence shows Domovoi's update probably
  built the image.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from domovoi import git_version, self_restart
from domovoi.container_guard import engine as cg_engine
from domovoi.container_guard import freeze, holds, store
from domovoi.container_guard.detect import RestartMemory, detect
from domovoi.container_guard.docker_cli import parse_created
from domovoi.container_guard.expected import ExpectedSet, mpd_expectations
from domovoi.container_guard.model import TAMPER, DockerView, Listed, Observed
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_container_guard_engine import guard_env  # noqa: F401 (fixture)

# The two images of incident #5.
OLD = "sha256:03c3a6ac9815822af16118f2ee5dcae6590b76d20aeb234d670a3f81c062a800"
NEW = "sha256:b05fcdd8915f3a2fd9b117ebf443fe62c40427ffcc507c05051d535f12877a7d"
HAND = "sha256:" + "e" * 64      # a docker build by hand, outside Domovoi
HAND2 = "sha256:" + "f" * 64
TAG = "domovoi-mpd:latest"
PREFIX = "domovoi-mpd-"
BASELINE_AT = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
RUN_STARTED = "2026-10-25T01:10:04Z"
RUN = {"status": "ok", "mode": "update", "mpd_changed": True, "started_at": RUN_STARTED,
       "finished_at": "2026-10-25T01:14:40Z", "from_sha": "8" * 40, "to_sha": "f" * 40}
# The same update's record from a script before the image record existed:
# it rebuilt nothing it said so about.
QUIET_RUN = dict(RUN, mpd_changed=False)


def write_update_record(path, **over) -> None:
    """What record_mpd_image in apply-update.sh writes (one key a line)."""
    doc = {"by": "update", "image_id": NEW, "tag": TAG, "built_at": "2026-10-25T01:12:31Z",
           "run_started_at": RUN_STARTED, "to_sha": "f" * 40}
    doc.update(over)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


@pytest.fixture
def records(tmp_path, monkeypatch):
    """Both root records under tmp_path, trusted as root's (the ownership
    rule has its own tests in test_container_guard_provision_record.py),
    and the update unit's result as the test sets it."""
    upd = tmp_path / "update-mpd-image.json"
    prov = tmp_path / "provision-mpd-image.json"
    monkeypatch.setattr(cg_engine, "UPDATE_MPD_RECORD", str(upd))
    monkeypatch.setattr(cg_engine, "PROVISION_MPD_RECORD", str(prov))
    monkeypatch.setattr(cg_engine, "_trusted_record", lambda _p: True)
    last = {"doc": dict(RUN)}
    monkeypatch.setattr(git_version, "load_last_update",
                        lambda: (dict(last["doc"]) if last["doc"] else None, None))
    monkeypatch.setattr(self_restart, "update_unit_installed", lambda: True)
    return {"update": upd, "provision": prov, "last": last}


# ─── the update unit's record as evidence ─────────────────────────────────

def test_the_update_record_vouches_for_exactly_the_image_it_names(records) -> None:
    write_update_record(records["update"])
    assert cg_engine.update_record_evidence(BASELINE_AT, NEW) is True
    assert cg_engine.vouched_rebuild(BASELINE_AT, NEW) == "update"
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, NEW) == "update-record"
    # A docker build by hand after the update's: no record names it.
    assert cg_engine.update_record_evidence(BASELINE_AT, HAND) is False
    assert cg_engine.vouched_rebuild(BASELINE_AT, HAND) is None


@pytest.mark.parametrize(
    "over, why",
    [
        ({"built_at": "2026-10-24T17:59:59Z"}, "built before the baseline (an old build re-tagged)"),
        ({"by": "provision"}, "not the update's (the installer has its own file)"),
        ({"by": None}, "no author"),
        ({"image_id": NEW[:-1]}, "not an image ID"),
        ({"built_at": "2026-10-25 01:12:31"}, "a stamp in another format"),
    ],
)
def test_an_update_record_that_does_not_say_so_is_no_evidence(records, over, why) -> None:
    write_update_record(records["update"], **over)
    assert cg_engine.update_record_evidence(BASELINE_AT, NEW) is False, why


def test_an_untrusted_update_record_is_no_evidence(records, monkeypatch) -> None:
    write_update_record(records["update"])
    monkeypatch.setattr(cg_engine, "_trusted_record", lambda _p: False)
    assert cg_engine.vouched_rebuild(BASELINE_AT, NEW) is None


def test_a_run_that_recorded_its_build_vouches_for_that_image_alone(records) -> None:
    # The run said mpd_changed and recorded NEW: a hand build that the tag
    # names afterwards is not covered by "the update rebuilt it".
    write_update_record(records["update"])
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, HAND) is None
    # A record from an earlier run, beside this run's result: this run left
    # none (the image ID couldn't be read), so its mpd_changed vouches as
    # before the record existed.
    write_update_record(records["update"], run_started_at="2026-10-01T00:00:00Z", image_id=OLD)
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, HAND) == "finished"
    # And without any record, as before.
    records["update"].unlink()
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, HAND) == "finished"


def test_the_record_paths_are_not_settings() -> None:
    from domovoi.config import settings

    assert cg_engine.UPDATE_MPD_RECORD == "/var/lib/domovoi-update/mpd-image.json"
    assert not any("mpd" in k and "record" in k for k in type(settings).model_fields)


# ─── at the core's start ──────────────────────────────────────────────────

@requires_db
async def test_the_core_s_start_adopts_the_update_s_recorded_build(guard_env, records) -> None:  # noqa: F811
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        assert await cg_engine.note_mpd_image(OLD, built=False) == "adopted"
        write_update_record(records["update"], built_at="2099-01-01T00:00:00Z")
        assert await cg_engine.note_mpd_image(NEW, built=False) == "update"
        assert "update unit" in guard.mpd_note
        async with session_scope() as s:
            b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
            p = await store.get_baseline(s, cg_engine.MPD_PREVIOUS_KEY)
        assert (b["value"], b["source"]) == (NEW, "update-unit:record")
        assert p["value"] == OLD, "the image it replaced is kept as the previous build"
        # A docker build by hand afterwards: reported, though the run said
        # mpd_changed.
        assert await cg_engine.note_mpd_image(HAND, built=False) == "changed"
    finally:
        cg_engine.reset_guard(None)


# ─── while the core runs ──────────────────────────────────────────────────

def _view(tag_id: str, *rooms: tuple[str, str]) -> DockerView:
    v = DockerView(ok=True)
    for room, image_id in rooms:
        name = PREFIX + room
        o = Observed(id=(name + "x" * 64)[:64], name=name, status="running", running=True,
                     image_id=image_id, config_image=TAG)
        v.listed[name] = Listed(id=o.id, name=name)
        v.containers[name] = o
        v.repo_digests[image_id] = (f"domovoi-mpd@{image_id}",)
    v.tag_ids[TAG] = tag_id
    return v


def _expected(*rooms: str, baseline: str = OLD) -> ExpectedSet:
    exp = ExpectedSet(mpd_prefix=PREFIX, mpd_tag_ref=TAG, mpd_image_id=baseline,
                      mpd_image_at=BASELINE_AT)
    exp.add(mpd_expectations([(r, PREFIX + r) for r in rooms], tag_ref=TAG, image_id=baseline,
                             warm_done=True))
    return exp


async def _checks(guard, clock, n=4):
    for _ in range(n):
        clock.t += 30
        await guard.check(force=True)


async def _open():
    async with session_scope() as s:
        return await store.open_incidents(s)


async def _baseline():
    async with session_scope() as s:
        b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
        p = await store.get_baseline(s, cg_engine.MPD_PREVIOUS_KEY)
    return (b or {}).get("value"), (b or {}).get("source"), (p or {}).get("value")


async def _seed_baseline(image_id: str = OLD) -> None:
    async with session_scope() as s:
        await store.set_baseline(s, cg_engine.MPD_BASELINE_KEY, image_id, "core-build")


@requires_db
async def test_update_rebuild_then_a_new_room_is_not_reported(guard_env, records) -> None:  # noqa: F811
    """Incident #5 with the fix: the update recorded what it built, the
    kitchen still runs the old image, the dining room is created from the
    tag half an hour later, before anything restarts the core."""
    guard, docker, clock, expected = guard_env
    await _seed_baseline()
    write_update_record(records["update"])
    expected["set"] = _expected("kitchen", "dining-room")
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW))
    await _checks(guard, clock)
    assert await _open() == []
    assert guard.status["findings"] == []
    assert await _baseline() == (NEW, "update-unit:record", OLD)
    assert "update unit" in (guard.mpd_note or "")
    # The kitchen, on Domovoi's previous build, waits to be recreated: not
    # tampering. Once it is, nothing changes either.
    docker.view = _view(NEW, ("kitchen", NEW), ("dining-room", NEW))
    await _checks(guard, clock)
    assert await _open() == []


@requires_db
async def test_a_room_made_before_the_record_is_read_is_taken_on_with_it(guard_env, records) -> None:  # noqa: F811
    """The lazy order: the new room's player exists before the first check
    that sees the tag move. The vouch is taken on before the rules run, in
    that same check, so the room is never even pending."""
    guard, docker, clock, expected = guard_env
    await _seed_baseline()
    write_update_record(records["update"])
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    clock.t += 30
    await guard.check(force=True)
    assert guard.status["pending"] == [] and guard.status["findings"] == []
    assert guard.debouncer.pending == {}


@requires_db
async def test_a_docker_build_by_hand_is_still_reported(guard_env, records) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    await _seed_baseline()
    write_update_record(records["update"])          # the update built NEW...
    expected["set"] = _expected("kitchen", "dining-room")
    # ...then someone with Docker access built their own under the tag.
    docker.view = _view(HAND, ("kitchen", OLD), ("dining-room", HAND))
    await _checks(guard, clock)
    rows = await _open()
    assert sorted((r["class"], r["kind"], r["container"] or "") for r in rows) == [
        ("tamper", "image", ""), ("tamper", "image", "domovoi-mpd-dining-room"),
    ]
    assert await _baseline() == (OLD, "core-build", None)
    async with session_scope() as s:
        assert await store.frozen_by(s), "the freeze holds"


@requires_db
async def test_a_hand_build_with_no_records_at_all_is_reported(guard_env, records) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    assert len(await _open()) == 2


@requires_db
async def test_an_installer_rebuild_with_the_core_running_and_a_new_room(guard_env, records) -> None:  # noqa: F811
    """Setup's repair rebuilt the image while the core ran; a room set up
    before provision.sh's restart of the core lands is not reported."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    records["provision"].write_text(json.dumps({
        "by": "provision", "image_id": NEW, "tag": TAG, "dockerfile_sha256": "f" * 64,
        "built_at": "2026-10-25T01:12:31Z"}), encoding="utf-8")
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    assert await _open() == []
    assert await _baseline() == (NEW, "provision", OLD)


@requires_db
async def test_open_incidents_about_a_vouched_image_are_cleared_by_domovoi(guard_env, records) -> None:  # noqa: F811
    """An install whose incident opened before the evidence was there: once
    a root record names exactly that image, the guard takes it on and
    closes those incidents, saying whose record it was; the freeze lifts."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    assert len(await _open()) == 2
    write_update_record(records["update"])
    await _checks(guard, clock, n=1)
    assert await _open() == []
    async with session_scope() as s:
        rows = await store.recent_incidents(s)
        assert await store.frozen_by(s) == []
    assert {r["cleared_by"] for r in rows} == {"Domovoi"}
    assert all("Domovoi's update built this image" in r["clear_note"] for r in rows)
    freeze.forget()


@requires_db
async def test_an_admin_clear_keeps_the_image_for_the_next_room(guard_env, records) -> None:  # noqa: F811
    """No record names the image (incident #5's box: the update ran a script
    from before the record). "Clear after review" is the way out, and it is
    the whole way: the next room made from that image isn't reported."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("kitchen", "dining-room")
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW))
    await _checks(guard, clock)
    rows = await _open()
    room = next(r for r in rows if r["container"] == "domovoi-mpd-dining-room")
    async with session_scope() as s:
        await store.clear(s, int(room["id"]), "admin", "the update rebuilt it")
    await _checks(guard, clock, n=1)
    assert await _baseline() == (NEW, "admin-cleared", OLD)
    assert "cleared" in (guard.mpd_note or "")
    # The tag's own incident stays open until the admin clears it too:
    # nothing about the image is cleared for them.
    assert [r["container"] for r in await _open()] == [None]
    # A new room on that image, and the kitchen still on the old one: quiet.
    expected["set"].add(mpd_expectations([("office", PREFIX + "office")], tag_ref=TAG,
                                         image_id=NEW, warm_done=True, image_id_previous=OLD))
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW), ("office", NEW))
    await _checks(guard, clock)
    assert [r["container"] for r in await _open()] == [None]
    # Another build by hand later: news again.
    docker.view = _view(HAND2, ("kitchen", OLD), ("dining-room", NEW), ("office", HAND2))
    await _checks(guard, clock)
    found = sorted((r["container"] or "", r["found"]["image_id"]) for r in await _open())
    assert ("", HAND2) in found and ("domovoi-mpd-office", HAND2) in found


# ─── the previous build, in the rules ─────────────────────────────────────

def test_a_room_on_the_previous_build_is_not_tampering_but_the_tag_must_be_current() -> None:
    exp = ExpectedSet(mpd_prefix=PREFIX, mpd_tag_ref=TAG, mpd_image_id=NEW,
                      mpd_image_previous=OLD)
    exp.add(mpd_expectations([("kitchen", PREFIX + "kitchen")], tag_ref=TAG, image_id=NEW,
                             warm_done=True, image_id_previous=OLD))
    view = _view(NEW, ("kitchen", OLD))
    view.listed[PREFIX + "gone"] = Listed(id="g" * 64, name=PREFIX + "gone")
    view.containers[PREFIX + "gone"] = Observed(id="g" * 64, name=PREFIX + "gone", status="running",
                                                running=True, image_id=OLD, config_image=TAG)
    det = detect(exp, view, now_wall=1.8e9, boot_wall=0.0, memory=RestartMemory(), now_mono=0.0)
    assert det.findings == []
    assert det.unrecognized == [PREFIX + "gone"], "a removed room's player on the old build"
    # Anything else in a room, or the tag on the previous build, is reported.
    det = detect(exp, _view(OLD, ("kitchen", HAND)), now_wall=1.8e9, boot_wall=0.0,
                 memory=RestartMemory(), now_mono=0.0)
    assert sorted(f.key for f in det.findings) == [
        f"tamper:image:{PREFIX}kitchen:{HAND}", f"tamper:mpd-image:{OLD}",
    ]


def test_no_previous_build_when_it_is_the_current_one() -> None:
    (e,) = mpd_expectations([("k", PREFIX + "k")], tag_ref=TAG, image_id=NEW, warm_done=True,
                            image_id_previous=NEW)
    assert e.image_id_previous is None


# ─── what the incident says ───────────────────────────────────────────────

MADE_IN_RUN = datetime(2026, 10, 25, 1, 12, 30, tzinfo=timezone.utc)
OLD_MADE = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def test_the_hint_when_the_image_was_made_during_the_update(records) -> None:
    hint = cg_engine.rebuild_hint(MADE_IN_RUN, OLD_MADE, BASELINE_AT)
    assert hint == (
        "This image was probably rebuilt by Domovoi's update at 2026-10-25 01:10 UTC; if you "
        "didn't build it yourself, it's safe to clear (clearing it makes it the music player "
        "image Domovoi expects)."
    )
    # The expected image gone from Docker: when it was recorded is the floor.
    assert cg_engine.rebuild_hint(MADE_IN_RUN, None, BASELINE_AT) == hint


@pytest.mark.parametrize(
    "made, expected_made, last, why",
    [
        (datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc), OLD_MADE, RUN, "made after the update finished"),
        (datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc), OLD_MADE, RUN, "made before it started"),
        (MADE_IN_RUN, datetime(2026, 10, 25, 1, 13, tzinfo=timezone.utc), RUN, "older than the expected image"),
        (MADE_IN_RUN, OLD_MADE, None, "no update record"),
        (MADE_IN_RUN, OLD_MADE, {"status": "ok"}, "a record without times"),
        (None, OLD_MADE, RUN, "Docker can't say when it was made"),
    ],
)
def test_no_hint_unless_the_evidence_shows_it(records, made, expected_made, last, why) -> None:
    records["last"]["doc"] = last
    assert cg_engine.rebuild_hint(made, expected_made, BASELINE_AT) is None, why


def test_no_hint_without_any_floor(records) -> None:
    assert cg_engine.rebuild_hint(MADE_IN_RUN, None, None) is None


def test_a_run_still_going_counts_up_to_now(records) -> None:
    records["last"]["doc"] = dict(RUN, status="running", finished_at=None)
    now = datetime(2026, 10, 25, 1, 20, tzinfo=timezone.utc)
    assert cg_engine.rebuild_hint(MADE_IN_RUN, OLD_MADE, BASELINE_AT, now=now)
    assert cg_engine.rebuild_hint(datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc), OLD_MADE,
                                  BASELINE_AT, now=now) is None


@requires_db
async def test_incident_5_now_says_the_update_probably_built_it(guard_env, records) -> None:  # noqa: F811
    """The replay of incident #5 on a box whose update left no record: the
    incident opens (nothing verifiable vouches), and its words say what the
    evidence shows and what to check."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()

    async def image_created(ids):
        return {NEW: MADE_IN_RUN, OLD: OLD_MADE}

    docker.image_created = image_created
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    rows = await _open()
    assert len(rows) == 2 and all(r["class"] == TAMPER for r in rows)
    room = next(r for r in rows if r["container"] == "domovoi-mpd-dining-room")
    assert room["summary"].startswith(
        "The music player for dining-room is running a different image than the one Domovoi expects."
    )
    assert "probably rebuilt by Domovoi's update at 2026-10-25 01:10 UTC" in room["summary"]
    assert "if you didn't build it yourself, it's safe to clear" in room["summary"]
    assert room["expected"]["image_id"] == OLD and room["found"]["image_id"] == NEW
    holds.clear()


@requires_db
async def test_a_hand_build_outside_any_update_gets_no_hint(guard_env, records) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    await _seed_baseline()

    async def image_created(ids):
        return {HAND: datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc), OLD: OLD_MADE}

    docker.image_created = image_created
    expected["set"] = _expected("dining-room")
    docker.view = _view(HAND, ("dining-room", HAND))
    await _checks(guard, clock)
    rows = await _open()
    assert len(rows) == 2
    assert all("probably" not in r["summary"] for r in rows)


def test_docker_s_created_times() -> None:
    assert parse_created("2026-10-25T01:12:30.123456789Z") == MADE_IN_RUN.replace(microsecond=0)
    assert parse_created("2026-10-25T01:12:30Z") == MADE_IN_RUN
    assert parse_created("2026-10-24T21:12:30-04:00") == MADE_IN_RUN
    assert parse_created("") is None and parse_created("yesterday") is None
