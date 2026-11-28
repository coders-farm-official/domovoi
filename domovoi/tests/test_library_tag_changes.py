"""``library_tag_changes`` (V088): every name song recognition changes is
logged with what it was, in the same statement, and a batch can be undone.

* the migration is the next one, append-only, and creates the table;
* a sweep logs each match (cause ``enrich``, one batch per sweep), and a
  match that changes no name logs nothing;
* the repair logs what it restores (cause ``repair``);
* an undo puts ``old`` back only where the track still has ``new``, never
  on a track a person edited (``manual`` / ``reviewed``), logs itself
  (cause ``undo``) and can itself be undone;
* the admin routes: the undo and the batch list.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import library_tag_changes as tc
from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import apply_v020
from domovoi.workers import library_enricher as le
from domovoi.workers.library_enricher import EnrichmentResult, Lookup, Verdict

ENRICHER = "domovoi.workers.library_enricher"
MIGRATIONS = Path(__file__).resolve().parents[2] / "domovoi" / "db" / "migrations"


# ═══ The migration (no DB) ════════════════════════════════════════════════


def test_v088_is_the_next_migration_and_creates_the_table() -> None:
    versions = sorted(int(m.group(1)) for p in MIGRATIONS.glob("V*__*.sql")
                      if (m := re.match(r"V(\d+)__", p.name)))
    assert len(versions) == len(set(versions)), "two migrations share a number"
    assert 88 in versions and 87 in versions
    sql = (MIGRATIONS / "V088__library_tag_changes.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS library_tag_changes" in sql
    for column in ("track_id", "batch_id", "cause", "old", "new", "changed_at", "undone_at"):
        assert re.search(rf"^\s+{column}\s", sql, re.M), column
    assert "'enrich', 'repair', 'rematch', 'undo'" in sql
    assert "ON DELETE CASCADE" in sql


def test_a_logged_update_is_one_statement() -> None:
    sql = tc.logged_update("title = :t")
    assert sql.count("UPDATE library_tracks") == 1
    assert "INSERT INTO library_tag_changes" in sql and ";" not in sql


def test_still_as_changed_compares_the_names_only() -> None:
    new = {"title": "A", "artist": "B", "album": None, "musicbrainz_recording_id": "r",
           "enrich_outcome": "matched"}
    assert tc.still_as_changed({"title": "A", "artist": "B", "album": None,
                                "musicbrainz_recording_id": "r", "enrich_outcome": "x"}, new)
    assert not tc.still_as_changed({"title": "A2", "artist": "B", "album": None,
                                    "musicbrainz_recording_id": "r"}, new)


# ═══ The sweep and the repair log (DB) ════════════════════════════════════


@pytest.fixture
async def lane(db_session, monkeypatch):
    await apply_v020()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_delay_sec", 0)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(f"{ENRICHER}.connectivity.current_probe", lambda: None)
    yield db_session


async def _insert(session, fp: str, title, artist, **cols) -> int:
    names = ["file_path", "title", "artist", *cols]
    params = {"file_path": fp, "title": title, "artist": artist, **cols}
    row = (await session.execute(text(
        f"INSERT INTO library_tracks ({', '.join(names)}) "
        f"VALUES ({', '.join(':' + n for n in names)}) RETURNING id"), params)).first()
    await session.commit()
    return int(row[0])


async def _changes(session, track_id: int | None = None):
    sql = "SELECT * FROM library_tag_changes"
    if track_id is not None:
        sql += " WHERE track_id = :t"
    return (await session.execute(text(sql + " ORDER BY id"), {"t": track_id})).mappings().all()


async def _no_shazam(_p):
    return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")


@requires_db
@pytest.mark.asyncio
async def test_a_sweep_logs_each_match_under_one_batch(lane, monkeypatch, tmp_path) -> None:
    from domovoi import egress

    a, b, same = tmp_path / "Linkin Park - In the End.mp3", tmp_path / "Toxic.mp3", tmp_path / "x.mp3"
    for p in (a, b, same):
        p.write_bytes(b"")
    ta = await _insert(lane, str(a), "In the End", "Linkin Park", album="Hybrid Theory")
    tb = await _insert(lane, str(b), "Toxic", "Britney Spears")
    # Already the provider's names and id: a match changes none of them.
    ts = await _insert(lane, str(same), "Creep", "Radiohead", musicbrainz_recording_id="r-creep")

    answers = {
        a.name: EnrichmentResult("In The End", "Linkin Park", musicbrainz_recording_id="r-end",
                                 source="acoustid"),
        b.name: EnrichmentResult("Toxic", "Britney Spears", musicbrainz_recording_id="r-toxic",
                                 source="acoustid"),
        same.name: EnrichmentResult("Creep", "Radiohead", musicbrainz_recording_id="r-creep",
                                    source="acoustid"),
    }

    async def ac(p):
        return Lookup(Verdict.MATCH, result=answers[p.name])

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    with egress.override_policy("always"):
        assert (await le.enrich_library())["matched"] == 3

    rows = await _changes(lane)
    assert {r["track_id"] for r in rows} == {ta, tb}            # the no-op isn't logged
    assert len({r["batch_id"] for r in rows}) == 1
    assert {r["cause"] for r in rows} == {"enrich"}
    end = next(r for r in rows if r["track_id"] == ta)
    assert end["old"]["title"] == "In the End" and end["old"]["musicbrainz_recording_id"] is None
    assert end["old"]["enrich_outcome"] is None and end["old"]["enriched_at"] is None
    assert end["new"]["title"] == "In The End" and end["new"]["musicbrainz_recording_id"] == "r-end"
    assert end["new"]["album"] == "Hybrid Theory" and end["undone_at"] is None
    assert ts not in {r["track_id"] for r in rows}


@requires_db
@pytest.mark.asyncio
async def test_the_repair_logs_what_it_restores(lane, tmp_path) -> None:
    f = tmp_path / "Linkin Park - In the End.mp3"
    f.write_bytes(b"")
    tid = await _insert(lane, str(f), "Linkin Park", "Boomin", musicbrainz_recording_id="r-wrong",
                        enrich_outcome="matched", year=2019, year_source="musicbrainz")
    await lane.execute(text("UPDATE library_tracks SET enriched_at = now()"))
    await lane.commit()
    assert (await le.repair_disagreeing())["restored"] == 1
    (row,) = await _changes(lane, tid)
    assert row["cause"] == "repair"
    assert (row["old"]["title"], row["old"]["artist"], row["old"]["year"]) == (
        "Linkin Park", "Boomin", 2019)
    assert (row["new"]["title"], row["new"]["musicbrainz_recording_id"], row["new"]["year"]) == (
        "In the End", None, None)


# ═══ Undo (DB) ════════════════════════════════════════════════════════════


async def _change(session, track_id: int, batch: str, set_sql: str, **params) -> None:
    await session.execute(text(tc.logged_update(set_sql)), {
        "id": track_id, "batch_id": batch, "cause": "rematch", **params})
    await session.commit()


async def _names(session, track_id: int):
    return tuple((await session.execute(text(
        "SELECT title, artist, album, musicbrainz_recording_id, enrich_outcome, "
        "enriched_at IS NULL AS waiting, year, genre_keys FROM library_tracks WHERE id = :i"),
        {"i": track_id})).one())


@requires_db
@pytest.mark.asyncio
async def test_undo_restores_only_unchanged_and_unedited_tracks(lane) -> None:
    batch = tc.new_batch_id()
    keep = await _insert(lane, "/m/a.mp3", "Ghetto Cowboy", "Bone", album="E. 1999 Eternal",
                         musicbrainz_recording_id="r-g", enrich_outcome="matched",
                         year=1995, year_source="musicbrainz", genre_keys=["hiphop"])
    later = await _insert(lane, "/m/b.mp3", "Ask Me Why", "The Beatles",
                          musicbrainz_recording_id="r-a", enrich_outcome="matched")
    hand = await _insert(lane, "/m/c.mp3", "Beyond the Pale", "Ed Sheeran",
                         musicbrainz_recording_id="r-p", enrich_outcome="matched")
    await lane.execute(text("UPDATE library_tracks SET enriched_at = now()"))
    await lane.commit()
    before = await _names(lane, keep)

    restore = ("title = :t, musicbrainz_recording_id = NULL, year = NULL, year_source = NULL, "
               "genre_keys = NULL, enriched_at = NULL, enrich_outcome = NULL")
    await _change(lane, keep, batch, restore, t="Mo'murda")
    await _change(lane, later, batch, restore, t="Drive My Car")
    await _change(lane, hand, batch, restore, t="Perfect")
    # Since then: a sweep re-matched one, a person edited another.
    await lane.execute(text("UPDATE library_tracks SET title = 'Drive My Car (Remastered)', "
                            "enrich_outcome = 'matched' WHERE id = :i"), {"i": later})
    await lane.execute(text("UPDATE library_tracks SET title = 'Perfect!', "
                            "enrich_outcome = 'manual' WHERE id = :i"), {"i": hand})
    await lane.commit()

    out = await tc.undo_batch(batch)
    assert (out["changes"], out["restored"], out["changed_since"], out["hand_edited"]) == (3, 1, 1, 1)
    assert await _names(lane, keep) == before                  # every column back
    assert (await _names(lane, later))[0] == "Drive My Car (Remastered)"
    assert (await _names(lane, hand))[:2] == ("Perfect!", "Ed Sheeran")

    rows = await _changes(lane, keep)
    assert rows[0]["undone_at"] is not None
    assert rows[1]["cause"] == "undo" and str(rows[1]["batch_id"]) == out["undo_batch_id"]
    # A second undo of the same batch finds nothing left to undo.
    assert (await tc.undo_batch(batch))["changes"] == 2        # the two skipped, still open
    assert await _names(lane, keep) == before
    # The undo can itself be undone.
    redo = await tc.undo_batch(out["undo_batch_id"])
    assert redo["restored"] == 1 and (await _names(lane, keep))[0] == "Mo'murda"


@requires_db
@pytest.mark.asyncio
async def test_undo_of_an_unknown_batch_does_nothing(lane) -> None:
    out = await tc.undo_batch(str(uuid.uuid4()))
    assert (out["changes"], out["restored"]) == (0, 0)


@requires_db
@pytest.mark.asyncio
async def test_the_admin_undo_and_list_routes(lane, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app

    batch = tc.new_batch_id()
    tid = await _insert(lane, "/m/a.mp3", "Ask Me Why", "The Beatles", enrich_outcome="matched")
    await _change(lane, tid, batch, "title = :t", t="Twist and Shout")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listed = (await client.get("/v1/admin/library/tag-changes")).json()["batches"]
            assert listed[0]["batch_id"] == batch and listed[0]["changes"] == 1
            assert (await client.post("/v1/admin/library/tag-changes/undo",
                                      json={"batch_id": "not-a-uuid"})).status_code == 422
            r = await client.post("/v1/admin/library/tag-changes/undo", json={"batch_id": batch})
            assert r.status_code == 200 and r.json()["restored"] == 1
            monkeypatch.setattr(le, "_sweep_running", True)
            busy = await client.post("/v1/admin/library/tag-changes/undo", json={"batch_id": batch})
            assert busy.status_code == 409
    assert (await _names(lane, tid))[0] == "Ask Me Why"
