"""The re-match (2026-11-28): find the tracks an earlier sweep renamed to
another recording, list them in a dry run that changes nothing, and apply
exactly that list on the owner's say-so.

* the audit (pure): a title against its file's names, recording ids shared
  by different songs, a title that is another file's song, bad encoding;
* the dry run's actions and its ``batch_id`` (derived from the list);
* against the database: the dry run changes nothing; an apply needs the
  dry run's ``batch_id``, restores the file's names, logs every change
  (cause ``rematch``) and puts the re-match rows back in the queue; the
  audit is clean afterwards; the next sweep picks the right sibling; the
  batch can be undone;
* the admin route.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi import library_tag_changes as tc
from domovoi.tests import media_writer as mw
from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import apply_v020
from domovoi.workers import library_enricher as le
from domovoi.workers import library_rematch as rm
from domovoi.workers.library_rematch import FileNames, Track

ENRICHER = "domovoi.workers.library_enricher"
BONE = "Bone Thugs-N-Harmony"
BONE_MB = "Bone Thugs‐n‐Harmony"


def _t(i, path, title, artist, rid=None, outcome="matched", album=None):
    return Track(i, path, title, artist, album, rid, outcome)


# Neutral copies of the audited shapes: a sibling's title by the same
# artist, two songs given one recording, a fine row, a hand edit.
LIBRARY = [
    _t(1, "/m/Ultimate/Mo'murda [aaaaaaaaaaa].mp3", "Ghetto Cowboy", BONE_MB, "r-ghetto-wrong"),
    _t(2, "/m/Ultimate/Ghetto Cowboy [bbbbbbbbbbb].mp3", "Ghetto Cowboy", BONE, "r-ghetto"),
    _t(3, "/m/Beatles/Drive My Car - Remastered 2009 [ccccccccccc].mp3", "Ask Me Why",
       "The Beatles", "r-ask"),
    _t(4, "/m/Beatles/Twist And Shout - 2023 Mix [ddddddddddd].mp3", "Ask Me Why",
       "The Beatles", "r-ask"),
    _t(5, "/m/Ed Sheeran - Perfect.mp3", "Perfect", "Ed Sheeran", "r-perfect"),
    _t(6, "/m/Ed Sheeran - Shivers.mp3", "Beyond the Pale", "Ed Sheeran", "r-pale", outcome="manual"),
    _t(7, "/m/track01.mp3", "Some Catalog Title", "Someone", "r-cat"),
    _t(8, "/m/Best/Should Be You [eeeeeeeeeee].mp3", "Should Be You", "Ne�Yo", "r-should"),
]


# ═══ The audit (pure) ═════════════════════════════════════════════════════


def test_the_audit_flags_wrong_titles_and_nothing_else() -> None:
    found = {f.id: f for f in rm.audit_library(LIBRARY)}
    assert set(found) == {1, 3, 4, 8}
    assert found[1].kind == "mismatch" and found[1].title_of == [2]
    assert found[3].shared_with == [4] and found[4].shared_with == [3]
    assert found[8].kind == "bad_encoding"


def test_shared_recordings_skip_copies_of_one_song() -> None:
    copies = [_t(1, "/m/a/Firestarter.mp3", "Firestarter", "The Prodigy", "r-f"),
              _t(2, "/m/b/The Prodigy - Firestarter (Official Video).mp3", "Firestarter",
                 "The Prodigy", "r-f")]
    assert rm.shared_recordings(copies) == {}


def test_the_file_tags_decide_over_the_file_name() -> None:
    # A file whose NAME says nothing but whose tags name the song.
    row = _t(1, "/m/track01.mp3", "Ghetto Cowboy", BONE_MB, "r-g")
    assert rm.audit_library([row]) == []
    found = rm.audit_library([row], {1: FileNames("Mo'murda", BONE)})
    assert [f.id for f in found] == [1]


def test_bad_text_is_found() -> None:
    assert rm.has_bad_text("Ne�Yo")
    assert rm.has_bad_text("Ne\x90Yo")
    assert rm.has_bad_text("Ne\udc90Yo")
    assert not rm.has_bad_text("Ne‐Yo", None, "")


# ═══ The plan (pure) ══════════════════════════════════════════════════════


def test_the_plan_proposes_an_action_for_each_flagged_row() -> None:
    files = {
        1: FileNames("Mo'murda", BONE, "E. 1999 Eternal"),
        # Upload names on a channel: nothing to judge a new answer by.
        3: FileNames("The Beatles - Drive My Car (Lyrics)", "Lyrics Channel"),
        4: FileNames("Twist And Shout - 2023 Mix", None),
    }
    rows = {r["id"]: r for r in rm.plan_rows(LIBRARY, files)}
    assert rows[1]["action"] == rm.ACTION_REMATCH
    assert rows[1]["action_label"] == "restore file names, then re-match"
    assert rows[1]["put_back"] == {"title": "Mo'murda", "artist": BONE, "album": "E. 1999 Eternal"}
    assert rows[1]["db"] == {"title": "Ghetto Cowboy", "artist": BONE_MB, "album": None,
                             "recording_id": "r-ghetto-wrong"}
    assert rows[1]["file_tags"] == {"title": "Mo'murda", "artist": BONE}
    assert rows[1]["title_belongs_to"] == [2]
    assert rows[3]["action"] == rm.ACTION_REVIEW
    assert rows[3]["put_back"]["title"] == "Drive My Car"
    assert rows[3]["put_back"]["artist"] == "The Beatles"   # the channel isn't put back
    assert rows[4]["action"] == rm.ACTION_REVIEW               # no artist tag
    assert rows[4]["put_back"]["artist"] == "The Beatles"
    assert rows[8]["action"] == rm.ACTION_BAD_ENCODING and rows[8]["put_back"] is None


def test_the_batch_id_names_exactly_the_list() -> None:
    rows = rm.plan_rows(LIBRARY, {})
    assert rm.batch_id_for(rows) == rm.batch_id_for(rm.plan_rows(LIBRARY, {}))
    assert rm.batch_id_for(rows) != rm.batch_id_for(rows[1:])
    changed = [dict(rows[0], put_back={"title": "x", "artist": "y", "album": None})] + rows[1:]
    assert rm.batch_id_for(rows) != rm.batch_id_for(changed)


# ═══ Against the database ═════════════════════════════════════════════════


@pytest.fixture
async def lane(db_session, monkeypatch, tmp_path):
    await apply_v020()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_delay_sec", 0)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(f"{ENRICHER}.connectivity.current_probe", lambda: None)
    monkeypatch.setattr("domovoi.workers.library_rematch.settings.music_dir", str(tmp_path))
    yield db_session


async def _insert(session, fp, title, artist, **cols) -> int:
    names = ["file_path", "title", "artist", "added_via", *cols]
    params = {"file_path": str(fp), "title": title, "artist": artist, "added_via": "manual", **cols}
    row = (await session.execute(text(
        f"INSERT INTO library_tracks ({', '.join(names)}) "
        f"VALUES ({', '.join(':' + n for n in names)}) RETURNING id"), params)).first()
    await session.commit()
    return int(row[0])


async def _table(session):
    return [tuple(r) for r in (await session.execute(text(
        "SELECT id, title, artist, album, musicbrainz_recording_id, enrich_outcome, "
        "enriched_at, year FROM library_tracks ORDER BY id"))).all()]


async def _library(session, tmp_path) -> dict[str, int]:
    folder = tmp_path / "Ultimate"
    mo = mw.write_mp3(folder / "Mo'murda [aaaaaaaaaaa].mp3", [
        mw.TIT2("Mo'murda"), mw.text_frame("TPE1", BONE), mw.text_frame("TALB", "E. 1999 Eternal")])
    ghetto = mw.write_mp3(folder / "Ghetto Cowboy [bbbbbbbbbbb].mp3", [
        mw.TIT2("Ghetto Cowboy"), mw.text_frame("TPE1", BONE)])
    upload = mw.write_mp3(tmp_path / "Drive My Car [ccccccccccc].mp3", [
        mw.TIT2("The Beatles - Drive My Car (Lyrics)"), mw.text_frame("TPE1", "Lyrics Channel")])
    hand = mw.write_mp3(tmp_path / "Shivers.mp3", [mw.TIT2("Shivers"), mw.text_frame("TPE1", "Ed Sheeran")])
    odd = mw.write_mp3(tmp_path / "Should Be You.mp3", [
        mw.TIT2("Should Be You"), mw.text_frame("TPE1", "Ne\x90Yo", encoding=0)])
    ids = {
        "mo": await _insert(session, mo, "Ghetto Cowboy", BONE_MB, album="E. 1999 Eternal",
                            musicbrainz_recording_id="r-ghetto-wrong", enrich_outcome="matched",
                            duration_sec=345, year=1997, year_source="musicbrainz"),
        "ghetto": await _insert(session, ghetto, "Ghetto Cowboy", BONE_MB,
                                musicbrainz_recording_id="r-ghetto", enrich_outcome="matched",
                                duration_sec=325),
        "upload": await _insert(session, upload, "Ask Me Why", "The Beatles",
                                musicbrainz_recording_id="r-ask", enrich_outcome="matched"),
        "hand": await _insert(session, hand, "Beyond the Pale", "Ed Sheeran", enrich_outcome="manual"),
        "odd": await _insert(session, odd, "Should Be You", "Ne‐Yo",
                             musicbrainz_recording_id="r-should", enrich_outcome="matched"),
    }
    await session.execute(text("UPDATE library_tracks SET enriched_at = now()"))
    await session.commit()
    return ids


@requires_db
@pytest.mark.asyncio
async def test_dry_run_apply_audit_sweep_and_undo(lane, monkeypatch, tmp_path) -> None:
    from domovoi import egress

    ids = await _library(lane, tmp_path)
    before = await _table(lane)

    plan = await rm.dry_run()
    assert plan["dry_run"] is True and await _table(lane) == before   # nothing changed
    assert (await lane.execute(text("SELECT count(*) FROM library_tag_changes"))).scalar_one() == 0
    rows = {r["id"]: r for r in plan["rows"]}
    assert set(rows) == {ids["mo"], ids["upload"], ids["odd"]}
    assert rows[ids["mo"]]["action"] == rm.ACTION_REMATCH
    assert rows[ids["mo"]]["file"] == "Ultimate/Mo'murda [aaaaaaaaaaa].mp3"
    assert rows[ids["upload"]]["action"] == rm.ACTION_REVIEW
    assert rows[ids["odd"]]["action"] == rm.ACTION_BAD_ENCODING
    assert plan["counts"] == {"restore_rematch": 1, "restore_review": 1, "bad_encoding": 1, "total": 3}

    # A batch id that isn't the dry run's changes nothing.
    with pytest.raises(rm.PlanChanged) as stale:
        await rm.apply(tc.new_batch_id())
    assert stale.value.batch_id == plan["batch_id"] and await _table(lane) == before

    out = await rm.apply(plan["batch_id"])
    assert (out["applied"], out["rematch"], out["review"]) == (2, 1, 1)
    got = {r[0]: r for r in await _table(lane)}
    mo = got[ids["mo"]]
    assert mo[1:6] == ("Mo'murda", BONE, "E. 1999 Eternal", None, None)
    assert mo[6] is None and mo[7] is None          # back in the queue; its year went with the id
    up = got[ids["upload"]]
    assert up[1:6] == ("Drive My Car", "The Beatles", None, None, "disagreed")
    assert got[ids["hand"]][1] == "Beyond the Pale"                    # a hand edit: untouched
    assert got[ids["odd"]][2] == "Ne‐Yo"                          # bad encoding: untouched
    cands = (await lane.execute(text(
        "SELECT reason, candidates FROM library_match_candidates WHERE track_id = :t"),
        {"t": ids["upload"]})).one()
    assert cands[0] == "disagreed" and cands[1][0]["title"] == "Ask Me Why"
    logged = (await lane.execute(text(
        "SELECT track_id, cause, CAST(batch_id AS text) FROM library_tag_changes ORDER BY id"))).all()
    assert {(r[0], r[1], r[2]) for r in logged} == {
        (ids["mo"], "rematch", plan["batch_id"]), (ids["upload"], "rematch", plan["batch_id"])}

    # The audit comes back clean (the bad encoding is still listed, never changed).
    again = await rm.dry_run()
    assert [r["action"] for r in again["rows"]] == [rm.ACTION_BAD_ENCODING]
    # The same batch id can't be applied twice.
    with pytest.raises(rm.PlanChanged):
        await rm.apply(plan["batch_id"])

    # The next sweep picks the right sibling when the fingerprint lists it.
    wrong = le.EnrichmentResult("Ghetto Cowboy", BONE_MB, musicbrainz_recording_id="r-ghetto",
                                source="acoustid", score=0.97, length_sec=325)
    right = le.EnrichmentResult("Mo’ Murda", BONE_MB, musicbrainz_recording_id="r-mo",
                                source="acoustid", score=0.9, length_sec=344)

    async def ac(_p):
        return le.Lookup(le.Verdict.MATCH, result=wrong, candidates=(wrong, right))

    async def no_shazam(_p):
        return le.Lookup(le.Verdict.NOT_ASKED)

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", no_shazam)
    with egress.override_policy("always"):
        assert (await le.enrich_library())["matched"] == 1
    mo = {r[0]: r for r in await _table(lane)}[ids["mo"]]
    assert mo[1:6] == ("Mo’ Murda", BONE_MB, "E. 1999 Eternal", "r-mo", "matched")

    # Undo the sweep, then the re-match: the library is as it was.
    sweep_batch = (await lane.execute(text(
        "SELECT CAST(batch_id AS text) FROM library_tag_changes WHERE cause = 'enrich'"))).scalar_one()
    assert (await tc.undo_batch(sweep_batch))["restored"] == 1
    assert (await tc.undo_batch(plan["batch_id"]))["restored"] == 2
    after = {r[0]: r for r in await _table(lane)}
    for row in before:
        assert after[row[0]][:6] == row[:6] and after[row[0]][7] == row[7]
    assert (await lane.execute(text(
        "SELECT count(*) FROM library_match_candidates WHERE decided IS NULL"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_apply_waits_for_a_running_sweep(lane, monkeypatch) -> None:
    monkeypatch.setattr(le, "_sweep_running", True)
    out = await rm.apply(tc.new_batch_id())
    assert out["skipped"] == "running" and out["applied"] == 0


@requires_db
@pytest.mark.asyncio
async def test_the_admin_rematch_route(lane, monkeypatch, tmp_path) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app

    ids = await _library(lane, tmp_path)
    started = []

    async def fake_sweep():
        started.append(True)
        return {"queued": True}

    monkeypatch.setattr("domovoi.main.admin_library_enrich", fake_sweep)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            dry = await client.post("/v1/admin/library/enrich/rematch")
            assert dry.status_code == 200 and dry.json()["dry_run"] is True
            batch = dry.json()["batch_id"]
            assert (await client.post("/v1/admin/library/enrich/rematch",
                                      json={"apply": True})).status_code == 422
            assert (await client.post("/v1/admin/library/enrich/rematch",
                                      json={"batch_id": batch})).status_code == 422
            stale = await client.post("/v1/admin/library/enrich/rematch",
                                      json={"apply": True, "batch_id": tc.new_batch_id()})
            assert stale.status_code == 409 and stale.json()["detail"]["batch_id"] == batch
            ok = await client.post("/v1/admin/library/enrich/rematch",
                                   json={"apply": True, "batch_id": batch})
            assert ok.status_code == 200, ok.text
            assert (ok.json()["applied"], ok.json()["sweep"]) == (2, "queued")
    assert started == [True]
    title = (await lane.execute(text("SELECT title FROM library_tracks WHERE id = :i"),
                                {"i": ids["mo"]})).scalar_one()
    assert title == "Mo'murda"
