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
  still is. Incidents already open stay open for the admin (a docker-group
  user can forge a root-owned record), with a note naming the record;
* an admin who clears the TAG's own incident while the tag names that
  image keeps it: the next room made from it isn't reported again. A
  room's incident covers only that container;
* Domovoi's previous build is exempt only until no room runs it;
* the incident says so when the update unit's own result shows its last
  run rebuilt the image without recording which (never from Docker's
  ``Created`` time, which a builder chooses).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from domovoi import git_version, self_restart
from domovoi.container_guard import engine as cg_engine
from domovoi.container_guard import freeze, holds, store
from domovoi.container_guard.detect import RestartMemory, detect
from domovoi.container_guard.expected import ExpectedSet, mpd_expectations
from domovoi.container_guard.model import TAMPER, DockerView, Finding, Listed, Observed
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from domovoi.tests.auth_testkit import _db  # noqa: F401 (fixture)
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


def _expected(*rooms: str, baseline: str = OLD, previous: str | None = None) -> ExpectedSet:
    exp = ExpectedSet(mpd_prefix=PREFIX, mpd_tag_ref=TAG, mpd_image_id=baseline,
                      mpd_image_at=BASELINE_AT, mpd_image_previous=previous)
    exp.add(mpd_expectations([(r, PREFIX + r) for r in rooms], tag_ref=TAG, image_id=baseline,
                             warm_done=True, image_id_previous=previous))
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
    assert await _baseline() == (NEW, "provision", None), "no room on the old build to exempt"


def _incident_finding(container: str | None, image_id: str, key: str | None = None) -> Finding:
    """A tamper image finding as detect writes it: the tag's (container
    None) or a container's."""
    if container is None:
        key = key or f"tamper:mpd-image:{image_id}"
        label, summary = "the music player image", "The music player image was replaced outside Domovoi."
    else:
        key = key or f"tamper:image:{container}:{image_id}"
        label, summary = f"the container {container}", f"The container {container} is running a different image."
    return Finding(key=key, cls=TAMPER, kind="image", severity="error", container=container,
                   owner="core", label=label, summary=summary,
                   expected={"image": TAG, "image_id": OLD}, found={"image": TAG, "image_id": image_id})


async def _open_finding(f: Finding) -> int:
    async with session_scope() as s:
        iid, _ = await store.open_incident(s, f)
    return iid


@requires_db
async def test_open_incidents_about_a_vouched_image_are_noted_and_left_open(guard_env, records) -> None:  # noqa: F811
    """An incident that opened before a record could be read is not closed
    when one appears: anyone in the docker group can write a root-owned
    file through a container, so lifting the freeze stays the admin's call.
    It gets a sentence naming the record; NEW detection is quiet."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    assert len(await _open()) == 2
    # A plugin's container on the same image ID: not the music player's,
    # never noted as Domovoi's build.
    plugin = await _open_finding(_incident_finding("jellyfin", NEW))
    write_update_record(records["update"])
    await _checks(guard, clock, n=2)
    rows = {r["id"]: r for r in await _open()}
    assert len(rows) == 3, "nothing closed by the record"
    noted = [r for i, r in rows.items() if i != plugin]
    assert all("Domovoi's update recorded building this image" in r["summary"] for r in noted)
    assert all(r["summary"].count("recorded building") == 1 for r in noted), "noted once"
    assert all("then clear" in r["summary"] for r in noted)
    assert "recorded building" not in rows[plugin]["summary"]
    async with session_scope() as s:
        assert await store.frozen_by(s), "the freeze stays until the admin clears"
    # (No room runs the old build: it isn't kept as the previous one.)
    assert await _baseline() == (NEW, "update-unit:record", None)
    # A room set up now, from the vouched image: no new incident.
    expected["set"].add(mpd_expectations([("office", PREFIX + "office")], tag_ref=TAG,
                                         image_id=NEW, warm_done=True))
    docker.view = _view(NEW, ("dining-room", NEW), ("office", NEW))
    await _checks(guard, clock)
    assert len(await _open()) == 3


@requires_db
async def test_clearing_a_room_s_incident_covers_that_container_only(guard_env, records) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("kitchen", "dining-room")
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW))
    await _checks(guard, clock)
    room = next(r for r in await _open() if r["container"] == "domovoi-mpd-dining-room")
    async with session_scope() as s:
        row = await store.clear(s, int(room["id"]), "admin", "the update rebuilt it")
        assert await cg_engine.adopt_cleared_tag(s, row, NEW) is None
    await _checks(guard, clock)
    assert await _baseline() == (OLD, "core-build", None), "nothing adopted"
    assert [r["container"] for r in await _open()] == [None], "the tag's own incident stays"
    # The next room set up from that image: reported, on its own.
    expected["set"].add(mpd_expectations([("office", PREFIX + "office")], tag_ref=TAG,
                                         image_id=OLD, warm_done=True))
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW), ("office", NEW))
    await _checks(guard, clock)
    assert sorted(r["container"] or "" for r in await _open()) == ["", "domovoi-mpd-office"]


@requires_db
async def test_clearing_the_tag_s_incident_keeps_the_image_for_the_next_room(guard_env, records) -> None:  # noqa: F811
    """No record names the image (incident #5's box: the update ran a
    script from before the record). Clearing the TAG's incident while the
    tag names that image is the way out, and the whole way: the next room
    made from it isn't reported."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("kitchen", "dining-room")
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW))
    await _checks(guard, clock)
    rows = await _open()
    tag = next(r for r in rows if r["container"] is None)
    room = next(r for r in rows if r["container"] == "domovoi-mpd-dining-room")
    async with session_scope() as s:
        row = await store.clear(s, int(tag["id"]), "admin", "the update rebuilt it")
        assert await cg_engine.adopt_cleared_tag(s, row, NEW) == NEW
        await store.clear(s, int(room["id"]), "admin", "same")
    assert await _baseline() == (NEW, "admin-cleared", OLD)
    # (gather_expected reads the baseline at every check; this fixture's set
    # is fixed, so it is given what the next gather would read.)
    expected["set"] = _expected("kitchen", "dining-room", "office", baseline=NEW, previous=OLD)
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW), ("office", NEW))
    await _checks(guard, clock)
    assert await _open() == []
    # Another build by hand later: news again.
    docker.view = _view(HAND2, ("kitchen", OLD), ("dining-room", NEW), ("office", HAND2))
    await _checks(guard, clock)
    found = sorted((r["container"] or "", r["found"]["image_id"]) for r in await _open())
    assert found == [("", HAND2), ("domovoi-mpd-office", HAND2)]


@requires_db
async def test_a_cleared_finding_adopts_only_the_tag_s_own_and_only_its_current_image(guard_env) -> None:  # noqa: F811
    await _seed_baseline()
    cases = [
        (_incident_finding(None, NEW), HAND, "the tag names another image now"),
        (_incident_finding(None, NEW), None, "the tag names nothing"),
        (_incident_finding(PREFIX + "kitchen", NEW), NEW, "a room's container"),
        (_incident_finding("jellyfin", NEW), NEW, "a plugin's container on that very ID"),
        (_incident_finding(None, NEW, key=f"tamper:unknown:{PREFIX}x:{NEW}"), NEW,
         "another kind of finding"),
        (_incident_finding(None, "sha256:" + "z" * 64), "sha256:" + "z" * 64, "not an image ID"),
    ]
    for f, tag_id, why in cases:
        iid = await _open_finding(f)
        async with session_scope() as s:
            row = await store.clear(s, iid, "admin", None)
            assert await cg_engine.adopt_cleared_tag(s, row, tag_id) is None, why
        assert await _baseline() == (OLD, "core-build", None), why


@requires_db
async def test_the_clear_route_keeps_the_tag_s_image(_db, records, monkeypatch) -> None:  # noqa: F811
    """POST …/clear on the tag's incident, the tag still naming its image:
    ``kept_image``; on a room's incident, nothing kept."""
    from domovoi.container_guard.docker_cli import DockerCli
    from domovoi.db.session import engine as db_engine
    from domovoi.tests.auth_testkit import bearer, claim_admin, core_client, web_client

    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents, container_guard_baselines RESTART IDENTITY"))
    freeze.forget()
    cg_engine.reset_guard(None)
    asked: list[str] = []

    async def tag_image_id(self, ref):
        asked.append(ref)
        return NEW

    monkeypatch.setattr(DockerCli, "tag_image_id", tag_image_id)
    try:
        await _seed_baseline()
        room = await _open_finding(_incident_finding(PREFIX + "dining-room", NEW))
        tag = await _open_finding(_incident_finding(None, NEW))
        async with web_client() as w:
            token = await claim_admin(w)
        async with core_client() as c:
            r = await c.post(f"/v1/admin/containers/incidents/{room}/clear", headers=bearer(token), json={})
            assert r.status_code == 200 and r.json()["kept_image"] is None
            assert asked == [], "a room's clear doesn't even look at the tag"
            assert await _baseline() == (OLD, "core-build", None)
            r = await c.post(f"/v1/admin/containers/incidents/{tag}/clear", headers=bearer(token),
                             json={"note": "our update rebuilt it"})
            assert r.status_code == 200, r.text
            assert r.json()["kept_image"] == NEW and r.json()["frozen"] is False
        assert asked == [TAG]
        assert await _baseline() == (NEW, "admin-cleared", OLD)
    finally:
        cg_engine.reset_guard(None)
        async with db_engine.begin() as conn:
            await conn.execute(text("TRUNCATE container_incidents, container_guard_baselines RESTART IDENTITY"))
        freeze.forget()


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


@requires_db
async def test_the_previous_build_is_forgotten_once_no_room_runs_it(guard_env, records) -> None:  # noqa: F811
    """The exemption covers the players that were on the old build when the
    baseline moved, until they are recreated: after that, a room on the old
    image is reported like any other."""
    guard, docker, clock, expected = guard_env
    await _seed_baseline()
    write_update_record(records["update"])
    expected["set"] = _expected("kitchen", "dining-room")
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW))
    await _checks(guard, clock)
    assert await _baseline() == (NEW, "update-unit:record", OLD)
    # Docker not answering is no data: nothing forgotten on it.
    assert guard._previous_unused(expected["set"], DockerView(ok=False, error="timed out")) is False
    # The kitchen recreated on the new image: the previous build goes.
    docker.view = _view(NEW, ("kitchen", NEW), ("dining-room", NEW))
    await _checks(guard, clock, n=1)
    assert await _baseline() == (NEW, "update-unit:record", None)
    assert await _open() == []
    # Someone runs the old image in a room's place again: reported.
    docker.view = _view(NEW, ("kitchen", OLD), ("dining-room", NEW))
    await _checks(guard, clock)
    assert [(r["container"], r["found"]["image_id"]) for r in await _open()] == [
        ("domovoi-mpd-kitchen", OLD)]


# ─── what the incident says ───────────────────────────────────────────────

HINT = ("Domovoi's update at 2026-10-25 01:10 UTC rebuilt the music player image without "
        "recording which image it built, so this may be that rebuild: check that nobody built "
        "or tagged domovoi-mpd:latest by hand")


def test_the_hint_when_the_update_rebuilt_without_a_record(records) -> None:
    hint = cg_engine.rebuild_hint(NEW, BASELINE_AT)
    assert hint is not None and hint.startswith(HINT) and hint.endswith("then clear.")
    assert "safe" not in hint
    # A run still going counts too.
    records["last"]["doc"] = dict(RUN, status="running", finished_at=None)
    assert cg_engine.rebuild_hint(NEW, BASELINE_AT) is not None
    # An update record from an earlier run, built before the baseline:
    # nothing about this one.
    write_update_record(records["update"], image_id=OLD, run_started_at="2026-10-01T00:00:00Z",
                        built_at="2026-10-01T00:05:00Z")
    records["last"]["doc"] = dict(RUN)
    assert cg_engine.rebuild_hint(NEW, BASELINE_AT) is not None


def _provision_record(path, image_id, built_at="2026-10-25T01:12:31Z") -> None:
    path.write_text(json.dumps({"by": "provision", "image_id": image_id, "tag": TAG,
                                "dockerfile_sha256": "f" * 64, "built_at": built_at}),
                    encoding="utf-8")


@pytest.mark.parametrize(
    "setup, why",
    [
        ("quiet", "the run didn't rebuild the music player (mpd_changed false)"),
        ("no-result", "no update record at all"),
        ("recorded", "the run recorded the image it built, and it is another one"),
        ("other-update-record", "an update record names another image built since the baseline"),
        ("other-provision-record", "the installer recorded building another image since"),
        ("before-baseline", "the run finished before the expected image was recorded"),
        ("no-start", "the run has no start time"),
    ],
)
def test_no_hint_unless_the_update_s_own_records_say_so(records, setup, why) -> None:
    if setup == "quiet":
        records["last"]["doc"] = dict(QUIET_RUN)
    elif setup == "no-result":
        records["last"]["doc"] = None
    elif setup == "recorded":
        write_update_record(records["update"], image_id=HAND)
    elif setup == "other-update-record":
        write_update_record(records["update"], image_id=HAND, run_started_at="2026-10-20T00:00:00Z")
    elif setup == "other-provision-record":
        _provision_record(records["provision"], HAND)
    elif setup == "before-baseline":
        records["last"]["doc"] = dict(RUN, finished_at="2026-10-24T17:00:00Z")
    elif setup == "no-start":
        records["last"]["doc"] = dict(RUN, started_at=None)
    assert cg_engine.rebuild_hint(NEW, BASELINE_AT) is None, why


@requires_db
async def test_incident_5_replayed_on_an_update_that_rebuilt_without_a_record(guard_env, records) -> None:  # noqa: F811
    """The replay of incident #5 on a box whose update said it rebuilt the
    music player (``mpd_changed: true``) with a script from before the
    record: the incident opens (nothing verifiable names the image), and
    its words say what the update's own result shows and what to check."""
    guard, docker, clock, expected = guard_env
    await _seed_baseline()
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    rows = await _open()
    assert len(rows) == 2 and all(r["class"] == TAMPER for r in rows)
    room = next(r for r in rows if r["container"] == "domovoi-mpd-dining-room")
    assert room["summary"].startswith(
        "The music player for dining-room is running a different image than the one Domovoi expects."
    )
    assert HINT in room["summary"] and "safe" not in room["summary"]
    assert room["expected"]["image_id"] == OLD and room["found"]["image_id"] == NEW
    holds.clear()


@requires_db
async def test_incident_5_replayed_on_an_update_that_did_not_rebuild(guard_env, records) -> None:  # noqa: F811
    """The same, on an update that said nothing about the music player
    (Dockerfile.mpd and mpd.conf unchanged since 2026-10-07, so most likely
    incident #5's own case): plain words, no hint."""
    guard, docker, clock, expected = guard_env
    records["last"]["doc"] = dict(QUIET_RUN)
    await _seed_baseline()
    expected["set"] = _expected("dining-room")
    docker.view = _view(NEW, ("dining-room", NEW))
    await _checks(guard, clock)
    rows = await _open()
    assert len(rows) == 2
    assert all("Domovoi's update at" not in r["summary"] for r in rows)


@requires_db
async def test_a_hand_build_after_a_recorded_update_gets_no_hint(guard_env, records) -> None:  # noqa: F811
    guard, docker, clock, expected = guard_env
    await _seed_baseline()
    write_update_record(records["update"])          # the update built NEW and said so
    expected["set"] = _expected("dining-room")
    docker.view = _view(HAND, ("dining-room", HAND))
    await _checks(guard, clock)
    rows = await _open()
    assert len(rows) == 2
    assert all("Domovoi's update at" not in r["summary"] for r in rows)


def test_the_hint_when_the_update_could_not_confirm_its_build(records) -> None:
    """The run built, but the ID its build wrote and root's look at the tag
    didn't agree, so it withheld its record (mpd_record: unconfirmed): it
    doesn't say it built without recording, it says it couldn't confirm."""
    records["last"]["doc"] = dict(RUN, mpd_record="unconfirmed")
    hint = cg_engine.rebuild_hint(NEW, BASELINE_AT)
    assert hint is not None
    assert hint.startswith(
        "Domovoi's update at 2026-10-25 01:10 UTC couldn't confirm which image it built, so this "
        "may be that rebuild: check that nobody built or tagged domovoi-mpd:latest by hand"
    )
    assert "without recording" not in hint and "safe" not in hint and hint.endswith("then clear.")
    # A run that recorded its build keeps the plain words for any other image.
    records["last"]["doc"] = dict(RUN, mpd_record="recorded")
    assert cg_engine.rebuild_hint(NEW, BASELINE_AT).startswith(HINT)


def test_the_update_result_carries_mpd_record(tmp_path, monkeypatch) -> None:
    from domovoi.config import settings

    path = tmp_path / "last-result.json"
    monkeypatch.setattr(settings, "update_result_file", str(path))
    for value, want in (("unconfirmed", "unconfirmed"), ("recorded", "recorded"),
                        (None, None), ("something else", None), (["x"], None)):
        path.write_text(json.dumps(dict(RUN, mpd_record=value)), encoding="utf-8")
        last, problem = git_version.load_last_update()
        assert problem is None and last["mpd_record"] == want, value
    path.write_text(json.dumps(dict(RUN)), encoding="utf-8")
    last, _ = git_version.load_last_update()
    assert "mpd_record" not in last, "a run from before the field"
