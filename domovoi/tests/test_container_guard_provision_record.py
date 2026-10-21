"""The installer's record of the music player images it builds
(scripts/linux/provision.sh, /var/lib/domovoi-provision/mpd-image.json)
as the container guard's evidence (docs/CONTAINER_GUARD.md, "The music
players").

2026-10-21, on a Windows install: a repair by Setup re-ran provision.sh,
whose images step rebuilt domovoi-mpd with a new image ID; the update unit
that ran next didn't touch the music player (its record said
``mpd_changed: false``), so nothing vouched for the new image and the guard
opened "The music player image was replaced outside Domovoi", freezing
plugin installs, updates, satellite upgrades and media prep. The record
vouches for exactly the image it names, built after the baseline, and only
when root alone can have written it; an image built by hand is still
reported.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from domovoi import git_version, self_restart
from domovoi.container_guard import engine as cg_engine
from domovoi.container_guard import holds, store
from domovoi.container_guard.expected import ExpectedSet, mpd_expectations
from domovoi.container_guard.model import DockerView, Listed, Observed
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_container_guard_engine import guard_env  # noqa: F401 (fixture)

OLD = "sha256:" + "0" * 64  # what the rooms run, the recorded baseline
NEW = "sha256:" + "b" * 64  # what provision.sh built
HAND = "sha256:" + "e" * 64  # a docker build by hand
TAG = "domovoi-mpd:latest"
BASELINE_AT = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
QUIET_UPDATE = {"status": "ok", "mpd_changed": False, "finished_at": "2026-10-21T00:21:02Z",
                "from_sha": "8" * 40, "to_sha": "1" * 40}


def write_record(path, **over) -> None:
    """What write_mpd_record in provision.sh writes (one key a line)."""
    doc = {"by": "provision", "image_id": NEW, "tag": TAG, "dockerfile_sha256": "f" * 64,
           "built_at": "2026-10-21T00:17:30Z"}
    doc.update(over)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


@pytest.fixture
def record(tmp_path, monkeypatch):
    """The record's path, trusted as root's (the ownership rule has its own
    tests below; this PC's files have no root owner)."""
    path = tmp_path / "mpd-image.json"
    monkeypatch.setattr(cg_engine, "PROVISION_MPD_RECORD", str(path))
    monkeypatch.setattr(cg_engine, "_trusted_record", lambda _p: True)
    # The incident's update record: readable, didn't touch the music player.
    monkeypatch.setattr(git_version, "load_last_update", lambda: (dict(QUIET_UPDATE), None))
    monkeypatch.setattr(self_restart, "update_unit_installed", lambda: True)
    return path


# ─── the record itself ────────────────────────────────────────────────────

def test_the_incident_a_setup_rebuild_after_a_quiet_update_is_vouched_for(record) -> None:
    write_record(record)
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is True
    # mpd_rebuild_evidence asks the installer's record first: the quiet
    # update record alone (mpd_changed false) was what reported it.
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, NEW) == "provision"
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT) is None


def test_a_hand_made_image_is_still_reported(record) -> None:
    # provision.sh built NEW; someone then ran docker build by hand (HAND).
    write_record(record)
    assert cg_engine.provision_evidence(BASELINE_AT, HAND) is False
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, HAND) is None


@pytest.mark.parametrize(
    "over, why",
    [
        ({"built_at": "2026-10-20T17:59:59Z"}, "built before the baseline (an old build re-tagged)"),
        ({"by": "someone"}, "not the installer's"),
        ({"by": None}, "no author"),
        ({"image_id": "sha256:" + "b" * 63}, "not an image ID"),
        ({"built_at": "2026-10-21 00:17:30"}, "a stamp in another format"),
        ({"built_at": None}, "no stamp"),
    ],
)
def test_a_record_that_does_not_say_so_is_no_evidence(record, over, why) -> None:
    write_record(record, **over)
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False, why
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, NEW) is None, why


def test_no_baseline_time_takes_any_valid_stamp(record) -> None:
    write_record(record)
    assert cg_engine.provision_evidence(None, NEW) is True
    naive = BASELINE_AT.replace(tzinfo=None)
    assert cg_engine.provision_evidence(naive, NEW) is True
    assert cg_engine.provision_evidence("2026-10-20", NEW) is False


def test_a_missing_garbled_or_oversized_record_is_no_evidence(record) -> None:
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False
    record.write_text("{not json", encoding="utf-8")
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False
    record.write_text(json.dumps(["provision", NEW]), encoding="utf-8")
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False
    write_record(record, padding="x" * cg_engine._RECORD_MAX_BYTES)
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False
    record.write_bytes(b"\xff\xfe")
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False


def test_an_untrusted_record_is_no_evidence(record, monkeypatch) -> None:
    write_record(record)
    monkeypatch.setattr(cg_engine, "_trusted_record", lambda _p: False)
    assert cg_engine.provision_evidence(BASELINE_AT, NEW) is False
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, NEW) is None


def test_the_update_unit_s_evidence_still_counts_without_the_installer_s(record, monkeypatch) -> None:
    # No installer record: an update that rebuilt the image vouches as before.
    rebuilt = dict(QUIET_UPDATE, mpd_changed=True)
    monkeypatch.setattr(git_version, "load_last_update", lambda: (rebuilt, None))
    assert cg_engine.mpd_rebuild_evidence(BASELINE_AT, NEW) == "finished"


# ─── who may have written it ──────────────────────────────────────────────

def st(uid, mode):
    return SimpleNamespace(st_uid=uid, st_mode=mode)


def test_only_root_alone_may_have_written_it() -> None:
    assert cg_engine._root_only(st(0, 0o100644))
    assert cg_engine._root_only(st(0, 0o040755))
    assert cg_engine._root_only(st(0, 0o100600))
    assert not cg_engine._root_only(st(1000, 0o100644)), "the service user's own file"
    assert not cg_engine._root_only(st(0, 0o100664)), "group-writable"
    assert not cg_engine._root_only(st(0, 0o100646)), "world-writable"
    assert not cg_engine._root_only(st(0, 0o041777)), "a sticky world-writable directory"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_a_file_this_user_owns_is_not_trusted(tmp_path) -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root: every file here is root's")
    path = tmp_path / "mpd-image.json"
    write_record(path)
    os.chmod(path, 0o644)
    assert cg_engine._trusted_record(path) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_a_link_is_not_trusted(tmp_path) -> None:
    target = tmp_path / "elsewhere.json"
    write_record(target)
    link = tmp_path / "mpd-image.json"
    link.symlink_to(target)
    assert cg_engine._trusted_record(link) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_never_trusted_on_windows(tmp_path) -> None:
    path = tmp_path / "mpd-image.json"
    write_record(path)
    assert cg_engine._trusted_record(path) is False


def test_the_record_path_is_not_a_setting() -> None:
    from domovoi.config import settings

    assert cg_engine.PROVISION_MPD_RECORD == "/var/lib/domovoi-provision/mpd-image.json"
    assert not any("provision" in k and "record" in k for k in type(settings).model_fields)


# ─── at the core's start, and while it runs ───────────────────────────────

@requires_db
async def test_the_core_s_start_adopts_the_installer_s_build(guard_env, record) -> None:  # noqa: F811
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        assert await cg_engine.note_mpd_image(OLD, built=False) == "adopted"
        # Built after the baseline was recorded (now()): stamp it later.
        write_record(record, built_at="2099-01-01T00:00:00Z")
        assert await cg_engine.note_mpd_image(NEW, built=False) == "provision"
        assert "installer" in guard.mpd_note
        async with session_scope() as s:
            b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
        assert (b["value"], b["source"]) == (NEW, "provision")
        # A docker build by hand afterwards: reported.
        assert await cg_engine.note_mpd_image(HAND, built=False) == "changed"
    finally:
        cg_engine.reset_guard(None)


@requires_db
async def test_the_core_s_start_reports_an_installer_record_from_before_the_baseline(  # noqa: F811
    guard_env, record,
) -> None:
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        write_record(record, built_at="2001-01-01T00:00:00Z")
        assert await cg_engine.note_mpd_image(OLD, built=False) == "adopted"
        assert await cg_engine.note_mpd_image(NEW, built=False) == "changed"
    finally:
        cg_engine.reset_guard(None)


def _room_view(tag_id: str) -> DockerView:
    v = DockerView(ok=True)
    o = Observed(id="k" * 64, name="domovoi-mpd-kitchen", status="running", running=True,
                 image_id=OLD, config_image=TAG)
    v.listed[o.name] = Listed(id=o.id, name=o.name)
    v.containers[o.name] = o
    v.tag_ids[TAG] = tag_id
    return v


def _rooms_expected() -> ExpectedSet:
    exp = ExpectedSet(mpd_prefix="domovoi-mpd-", mpd_tag_ref=TAG, mpd_image_id=OLD,
                      mpd_image_at=BASELINE_AT)
    exp.add(mpd_expectations([("kitchen", "domovoi-mpd-kitchen")], tag_ref=TAG, image_id=OLD,
                             warm_done=True))
    return exp


async def _checks(guard, clock, n=4):
    for _ in range(n):
        clock.t += 30
        await guard.check(force=True)


@requires_db
async def test_a_running_core_holds_the_tag_the_installer_rebuilt(guard_env, record) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    write_record(record)
    expected["set"] = _rooms_expected()
    docker.view = _room_view(NEW)
    await _checks(guard, clock)
    async with session_scope() as s:
        assert await store.open_incidents(s) == []
    assert guard.status["findings"] == []
    # The room still runs the recorded image: nothing about it either.
    assert "Domovoi's installer rebuilt" in (expected["set"].mpd_tag_hold or "")
    holds.clear()


@requires_db
async def test_a_running_core_still_reports_a_hand_made_tag(guard_env, record) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    write_record(record)  # the installer built NEW; the tag now names HAND
    expected["set"] = _rooms_expected()
    docker.view = _room_view(HAND)
    await _checks(guard, clock)
    async with session_scope() as s:
        rows = await store.open_incidents(s)
    assert [(r["class"], r["kind"], r["container"]) for r in rows] == [("tamper", "image", None)]
    assert rows[0]["summary"] == "The music player image was replaced outside Domovoi."
