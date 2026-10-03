"""Household "also called" names — the repository and policy
(``domovoi/db/library_aliases.py``, V019 ``library_aliases``).

Owner decisions 2026-10-02: an artist, album or song can have ANY number of
other names (one row per name → target), a name means ONE thing in the
household (adding it again for something else is a "replace it?"
question, never a second meaning), any paired device may add, and only an
admin may remove or replace a name someone else added. MusicBrainz names
never displace a household name; removing one hides it.

The first half is DB-free (the policy, the name rules, the library-name
scan as a pure fold). The second half runs on the lane's database with
V019 applied by ``library_aliases_testkit.apply_v019``.

Every name below is synthetic (checked clear of the held-out spoken-match
gate before use).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.db import library_aliases as repo
from domovoi.db.library_aliases import Actor, AliasRow, LibraryNames, Target
from domovoi.handlers.shared.spoken_names import alias_key
from domovoi.tests.conftest import requires_db

ADMIN = Actor(kind="admin")
DEV_A = Actor(kind="device", device_id="test-alias-dev-a")
DEV_B = Actor(kind="device", device_id="test-alias-dev-b")
NO_ID = Actor(kind="device", device_id=None)


def _row(**kw) -> AliasRow:
    base = dict(
        id=1, alias="gramps", alias_key="gramps", key_version=1, target_type="artist",
        target_key="hearthensemble", target_name="Hearth Ensemble", target_artist_key=None,
        target_track_id=None, source="manual", created_by_kind="device",
        created_by_device="test-alias-dev-a", created_by_room=None, created_by_person=None,
        mb_artist_id=None, mb_alias_type=None, suppressed=False,
        created_at=datetime(2026, 10, 3, tzinfo=timezone.utc), updated_at=None, device_name="Kitchen tablet",
    )
    base.update(kw)
    return AliasRow(**base)


# ─── The policy (DB-free) ──────────────────────────────────────────────────


def test_admin_may_modify_anything() -> None:
    for row in (_row(), _row(created_by_kind="voice", created_by_device=None, created_by_room="kitchen"),
                _row(created_by_kind="admin", created_by_device=None)):
        assert repo.may_modify(row, ADMIN)


def test_a_device_may_modify_only_what_it_added() -> None:
    mine = _row()
    assert repo.may_modify(mine, DEV_A)
    assert not repo.may_modify(mine, DEV_B)
    assert not repo.may_modify(mine, NO_ID)
    # an add that carried no device id (the Android app today) is an admin's
    anonymous = _row(created_by_device=None)
    assert not repo.may_modify(anonymous, NO_ID)
    assert not repo.may_modify(anonymous, DEV_A)
    # a device never inherits an admin's or a voice's name
    assert not repo.may_modify(_row(created_by_kind="admin", created_by_device=None), DEV_A)
    assert not repo.may_modify(_row(created_by_kind="voice", created_by_device=None, created_by_room="den"), DEV_A)


def test_anyone_may_modify_a_musicbrainz_name() -> None:
    mb = _row(source="musicbrainz", created_by_kind="system", created_by_device=None)
    for actor in (DEV_A, DEV_B, NO_ID, Actor(kind="voice", room_id="den")):
        assert repo.may_modify(mb, actor)


def test_voice_rules_follow_the_person_then_the_room() -> None:
    by_seven = _row(created_by_kind="voice", created_by_device=None, created_by_room="den", created_by_person=7)
    assert repo.may_modify(by_seven, Actor(kind="voice", room_id="kitchen", person_id=7))
    assert not repo.may_modify(by_seven, Actor(kind="voice", room_id="den", person_id=8))
    assert not repo.may_modify(by_seven, Actor(kind="voice", room_id="den"))   # unrecognized speaker
    nobody = _row(created_by_kind="voice", created_by_device=None, created_by_room="den")
    assert repo.may_modify(nobody, Actor(kind="voice", room_id="den"))
    assert repo.may_modify(nobody, Actor(kind="voice", room_id="den", person_id=3))
    assert not repo.may_modify(nobody, Actor(kind="voice", room_id="kitchen"))
    assert not repo.may_modify(_row(created_by_kind="voice", created_by_device=None),
                               Actor(kind="voice", room_id=None))
    # a voice never removes a dashboard's name
    assert not repo.may_modify(_row(), Actor(kind="voice", room_id="den"))


def test_added_by_never_shows_another_devices_raw_id() -> None:
    assert repo.added_by(_row()) == "Kitchen tablet"
    assert repo.added_by(_row(device_name=None)) == "a device"
    assert repo.added_by(_row(created_by_kind="admin")) == "admin"
    assert repo.added_by(_row(created_by_kind="voice", created_by_room="den")) == "voice in den"
    assert repo.added_by(_row(source="musicbrainz", created_by_kind="system")) == "MusicBrainz"
    pub = repo.public_alias(_row(), DEV_B)
    assert "test-alias-dev-a" not in repr(pub)
    assert pub["can_remove"] is False and repo.public_alias(_row(), DEV_A)["can_remove"] is True


def test_alias_text_rules() -> None:
    artist = Target(type="artist", name="Hearth Ensemble", key="hearthensemble")
    assert repo.normalize_alias("  the   kindlers. ") == "the kindlers"
    _, key, refusal = repo.check_alias_text("Gramps!", artist)
    assert (key, refusal) == ("gramps", None)
    assert repo.check_alias_text("   ", artist)[2].code == "empty"
    assert repo.check_alias_text("!!!", artist)[2].code == "empty"          # all punctuation
    assert repo.check_alias_text("~ ~ -", artist)[2].code == "nothing_to_say"
    assert repo.check_alias_text("x" * 121, artist)[2].code == "too_long"
    assert repo.check_alias_text("x" * 120, artist)[2] is None
    # the target's own name, however it is spelled, is not another name
    own = repo.check_alias_text("hearth-ensemble", artist)[2]
    assert own.code == "already_its_name" and "Hearth Ensemble" in own.message
    song = Target(type="track", name="Warm Stones (Live)", track_id=1)
    assert repo.check_alias_text("warm stones", song)[2].code == "already_its_name"


def test_target_round_trips_through_a_parked_payload() -> None:
    for t in (Target(type="artist", name="Hearth Ensemble", key="hearthensemble"),
              Target(type="album", name="Greatest Hits", key="greatesthits", artist_key="emberchoir",
                     artist_name="Ember Choir"),
              Target(type="track", name="Warm Stones", track_id=12, artist_name="Hearth Ensemble")):
        assert Target.from_dict(t.to_dict()) == t
    assert Target.from_dict({"type": "track", "name": "x"}) is None
    assert Target.from_dict({"type": "artist", "name": "x", "key": ""}) is None
    assert Target.from_dict("nope") is None


# ─── The library as names (DB-free fold) ───────────────────────────────────

LIBRARY = [
    # (id, file_path, title, artist, album)
    (1, "/music/Hearth Ensemble/By The Hearth/01 Warm Stones.mp3", "Warm Stones", "Hearth Ensemble", "By The Hearth"),
    (2, "/music/Hearth Ensemble/By The Hearth/02 Kindling.mp3", "Kindling", "Hearth Ensemble", "By The Hearth"),
    (3, "/music/Hearth Ensemble/By The Hearth/03 Lemonade.mp3", "Lemonade", "Hearth Ensemble", "By The Hearth"),
    (4, "/music/Ember Choir/Greatest Hits/01 Glow Street.mp3", "Glow Street", "Ember Choir", "Greatest Hits"),
    (5, "/music/Night Shift Orchestra/Greatest Hits/01 Coal Lane.mp3", "Coal Lane", "Night Shift Orchestra", "Greatest Hits"),
    (6, "/music/Earth, Wind & Fire/Fireside/01 Ash Avenue.mp3", "Ash Avenue", "Earth, Wind & Fire", "Fireside"),
    (7, "/music/Ember Choir/Lemonade/01 Cinders.mp3", "Cinders", "Ember Choir", "Lemonade"),
    (8, "/music/Ember Choir/Lemonade/02 In Too Deep.mp3", "In Too Deep", "Ember Choir feat. Night Shift Orchestra", "Lemonade"),
    (9, "/music/loose/Tech N9ne - Bugatti.mp3", "Tech N9ne - Bugatti", None, None),
]


def _names() -> LibraryNames:
    names = LibraryNames()
    for _id, _fp, title, artist, album in LIBRARY:
        names.add_row(title, artist, album)
    return names


def test_library_names_fold_every_kind_of_name() -> None:
    names = _names()
    assert "hearthensemble" in names.artists and "emberchoir" in names.artists
    assert "techn9ne" in names.artists                     # filename-derived "Artist - Title"
    assert alias_key("Earth, Wind & Fire") in names.credits
    assert "bugatti" in names.titles and "lemonade" in names.titles
    # two "Greatest Hits", one per primary artist
    assert set(names.albums["greatesthits"]["Greatest Hits"]) == {"emberchoir", "nightshiftorchestra"}


def test_lookup_candidates_follow_the_fetch_rule() -> None:
    cands = repo.lookup_candidates(_names())
    assert {"hearthensemble", "emberchoir", "nightshiftorchestra", "techn9ne"} <= set(cands)
    assert alias_key("Earth, Wind & Fire") in cands             # band-like credit, as a whole
    assert "fire" not in cands and "earth" not in cands          # never a fragment on its own
    assert alias_key("Ember Choir feat. Night Shift Orchestra") not in cands
    assert repo.band_like_credit("Earth, Wind & Fire")
    assert not repo.band_like_credit("Ember Choir feat. Night Shift Orchestra")
    assert not repo.band_like_credit("Ember Choir")


def test_shadows_name_the_other_library_names_an_alias_takes_over() -> None:
    names = _names()
    ember = Target(type="artist", name="Ember Choir", key="emberchoir")
    assert repo.shadows_for("kindling", ember, names) == [{"type": "title", "name": "Kindling"}]
    album = Target(type="album", name="Lemonade", key="lemonade", artist_key="emberchoir")
    # "Lemonade" the song by Hearth Ensemble is shadowed; the album itself is not
    assert repo.shadows_for("lemonade", album, names) == [{"type": "title", "name": "Lemonade"}]
    assert repo.shadows_for("nothinghere", ember, names) == []


# ─── On the database ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def lib(db_session):
    """The synthetic library on the lane, V019 applied and empty, two
    registered devices and one person."""
    from domovoi.tests.library_aliases_testkit import apply_v019

    await apply_v019()
    for tid, fp, title, artist, album in LIBRARY:
        await db_session.execute(
            text("INSERT INTO library_tracks (id, file_path, title, artist, album) VALUES (:i, :f, :t, :a, :al)"),
            {"i": tid, "f": fp, "t": title, "a": artist, "al": album},
        )
    await db_session.execute(text(
        "INSERT INTO devices (device_id, name) VALUES ('test-alias-dev-a', 'Kitchen tablet'), "
        "('test-alias-dev-b', 'Den phone') ON CONFLICT (device_id) DO UPDATE SET name = EXCLUDED.name"
    ))
    await db_session.execute(text("INSERT INTO people (id, name) VALUES (7, 'Test Person')"))
    await db_session.commit()
    yield db_session
    await db_session.rollback()
    await db_session.execute(text("DELETE FROM devices WHERE device_id LIKE 'test-alias-dev-%'"))
    await db_session.commit()


HEARTH = Target(type="artist", name="Hearth Ensemble", key="hearthensemble")
EMBER = Target(type="artist", name="Ember Choir", key="emberchoir")


async def _add(s, alias, target=HEARTH, actor=DEV_A, **kw):
    out = await repo.add_alias(s, alias=alias, target=target, actor=actor, source=kw.pop("source", "manual"), **kw)
    await s.commit()
    return out


async def _mb(s, alias, target_key="hearthensemble", target_name="Hearth Ensemble", suppressed=False):
    """A MusicBrainz row as the fetch writes it (ON CONFLICT DO NOTHING)."""
    row = (await s.execute(text(
        "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, target_name, source, "
        "created_by_kind, mb_artist_id, mb_alias_type, suppressed) VALUES (:a, :k, 'artist', :tk, :tn, "
        "'musicbrainz', 'system', 'mbid-1', 'Artist name', :sup) ON CONFLICT (alias_key) DO NOTHING RETURNING id"
    ), {"a": alias, "k": alias_key(alias), "tk": target_key, "tn": target_name, "sup": suppressed})).first()
    await s.commit()
    return None if row is None else int(row[0])


@requires_db
@pytest.mark.asyncio
async def test_one_target_any_number_of_names(lib) -> None:
    """Owner decision 6: one row per name → target; five names on one band."""
    for name in ("gramps", "the kindlers", "hearth band", "fireside crew", "warmies"):
        out = await _add(lib, name)
        assert out.status == "added", (name, out)
    rows = await repo.aliases_for_target(lib, HEARTH)
    assert [r.alias for r in rows] == ["gramps", "the kindlers", "hearth band", "fireside crew", "warmies"]
    assert {r.created_by_device for r in rows} == {"test-alias-dev-a"}
    assert all(r.source == "manual" and r.created_by_kind == "device" for r in rows)


@requires_db
@pytest.mark.asyncio
async def test_a_name_means_one_thing_and_the_same_target_is_a_no_op(lib) -> None:
    first = await _add(lib, "Gramps")
    again = await _add(lib, "gramps!", actor=DEV_B)        # same key, same target, another device
    assert again.status == "exists" and again.row.id == first.row.id
    taken = await _add(lib, "GRAMPS", target=EMBER, actor=DEV_B)
    assert taken.status == "taken" and taken.existing.id == first.row.id
    assert taken.can_replace is False                       # DEV_B did not add it
    assert (await _add(lib, "gramps", target=EMBER, actor=DEV_A)).can_replace is True
    assert (await _add(lib, "gramps", target=EMBER, actor=ADMIN)).can_replace is True
    count = (await lib.execute(text("SELECT count(*) FROM library_aliases"))).scalar_one()
    assert count == 1


@requires_db
@pytest.mark.asyncio
async def test_replace_needs_permission_and_moves_the_row_in_place(lib) -> None:
    first = await _add(lib, "gramps")
    refused = await _add(lib, "gramps", target=EMBER, actor=DEV_B, replace=True)
    assert refused.status == "forbidden" and refused.message == repo.ONLY_ADMIN_CHANGE
    still = await repo.get_alias(lib, first.row.id)
    assert still.target_key == "hearthensemble"
    moved = await _add(lib, "gramps", target=EMBER, actor=ADMIN, replace=True)
    assert moved.status == "replaced" and moved.row.id == first.row.id
    assert (moved.row.target_key, moved.row.target_name, moved.row.created_by_kind) == ("emberchoir", "Ember Choir", "admin")
    assert moved.row.created_by_device is None
    # the owner may replace its own
    own = await _add(lib, "warmies")
    assert (await _add(lib, "warmies", target=EMBER, actor=DEV_A, replace=True)).status == "replaced"
    assert (await repo.get_alias(lib, own.row.id)).target_key == "emberchoir"


@requires_db
@pytest.mark.asyncio
async def test_musicbrainz_never_displaces_a_household_name(lib) -> None:
    await _add(lib, "gramps")
    assert await _mb(lib, "Gramps", target_key="emberchoir", target_name="Ember Choir") is None
    row = await repo.find_alias(lib, "gramps")
    assert row.source == "manual" and row.target_key == "hearthensemble"


@requires_db
@pytest.mark.asyncio
async def test_a_household_add_over_a_live_musicbrainz_name_asks_and_anyone_may_replace(lib) -> None:
    mb_id = await _mb(lib, "hearth band")
    same = await _add(lib, "Hearth Band", actor=DEV_B)
    assert same.status == "exists" and same.row.id == mb_id      # same target: nothing to do
    other = await _add(lib, "hearth band", target=EMBER, actor=DEV_B)
    assert other.status == "taken" and other.can_replace is True
    done = await _add(lib, "hearth band", target=EMBER, actor=DEV_B, replace=True)
    assert done.status == "replaced" and done.row.id == mb_id
    assert (done.row.source, done.row.created_by_kind, done.row.mb_artist_id) == ("manual", "device", None)


@requires_db
@pytest.mark.asyncio
async def test_a_hidden_musicbrainz_name_is_taken_over_in_place(lib) -> None:
    mb_id = await _mb(lib, "warmies", target_key="emberchoir", target_name="Ember Choir", suppressed=True)
    assert await repo.find_alias(lib, "warmies") is None             # hidden rows are not names
    out = await _add(lib, "Warmies")
    assert out.status == "added" and out.row.id == mb_id
    assert (out.row.source, out.row.suppressed, out.row.target_key) == ("manual", False, "hearthensemble")


@requires_db
@pytest.mark.asyncio
async def test_removing_a_musicbrainz_name_hides_it_and_a_household_one_deletes_it(lib) -> None:
    mb_id = await _mb(lib, "hearth band")
    out = await repo.remove_alias(lib, mb_id, DEV_B)                   # anyone
    await lib.commit()
    assert out.status == "suppressed"
    row = await repo.get_alias(lib, mb_id)
    assert row is not None and row.suppressed
    assert (await repo.remove_alias(lib, mb_id, ADMIN)).status == "not_found"
    # the fetch's ON CONFLICT DO NOTHING keeps it from coming back
    assert await _mb(lib, "hearth band") is None

    mine = await _add(lib, "gramps")
    assert (await repo.remove_alias(lib, mine.row.id, DEV_B)).status == "forbidden"
    assert (await repo.remove_alias(lib, mine.row.id, NO_ID)).status == "forbidden"
    assert (await repo.remove_alias(lib, mine.row.id, DEV_A)).status == "removed"
    await lib.commit()
    assert await repo.get_alias(lib, mine.row.id) is None
    assert (await repo.remove_alias(lib, 99999, ADMIN)).status == "not_found"


@requires_db
@pytest.mark.asyncio
async def test_voice_adds_carry_room_and_person(lib) -> None:
    by_seven = Actor(kind="voice", room_id="den", person_id=7)
    out = await _add(lib, "gramps", actor=by_seven, source="voice")
    assert (out.row.source, out.row.created_by_kind, out.row.created_by_room, out.row.created_by_person) == (
        "voice", "voice", "den", 7)
    assert out.row.created_by_device is None
    assert repo.added_by(out.row) == "voice in den"
    assert (await repo.remove_alias(lib, out.row.id, Actor(kind="voice", room_id="den"))).status == "forbidden"
    assert (await repo.remove_alias(lib, out.row.id, Actor(kind="voice", room_id="kitchen", person_id=7))).status == "removed"


@requires_db
@pytest.mark.asyncio
async def test_an_alias_may_shadow_another_library_name_and_says_so(lib) -> None:
    out = await _add(lib, "kindling", target=EMBER)
    assert out.status == "added"
    assert out.shadows == [{"type": "title", "name": "Kindling"}]
    own = await _add(lib, "ember-choir", target=EMBER)
    assert own.status == "invalid" and own.code == "already_its_name"


@requires_db
@pytest.mark.asyncio
async def test_resolve_target_finds_artists_credits_albums_and_songs(lib) -> None:
    t = await repo.resolve_target(lib, "artist", name="hearth ensemble")
    assert (t.key, t.name) == ("hearthensemble", "Hearth Ensemble")
    t = await repo.resolve_target(lib, "artist", name="Tech Nine")      # a spoken spelling
    assert (t.key, t.name) == ("techn9ne", "Tech N9ne")
    t = await repo.resolve_target(lib, "artist", name="Earth Wind and Fire")
    assert t.key == alias_key("Earth, Wind & Fire") and t.name == "Earth, Wind & Fire"
    assert await repo.resolve_target(lib, "artist", name="Nobody Here") is None
    # two "Greatest Hits": the artist pins one
    t = await repo.resolve_target(lib, "album", name="Greatest Hits", artist_name="Night Shift Orchestra")
    assert (t.key, t.artist_key, t.artist_name) == ("greatesthits", "nightshiftorchestra", "Night Shift Orchestra")
    assert await repo.resolve_target(lib, "album", name="Greatest Hits", artist_name="Hearth Ensemble") is None
    t = await repo.resolve_target(lib, "album", name="greatest hits")
    assert (t.key, t.artist_key) == ("greatesthits", None)
    t = await repo.resolve_target(lib, "track", track_id=9)
    assert (t.name, t.artist_name, t.track_id) == ("Bugatti", "Tech N9ne", 9)
    assert await repo.resolve_target(lib, "track", track_id=4242) is None


@requires_db
@pytest.mark.asyncio
async def test_album_names_pin_to_their_artist(lib) -> None:
    ember_hits = await repo.resolve_target(lib, "album", name="Greatest Hits", artist_name="Ember Choir")
    night_hits = await repo.resolve_target(lib, "album", name="Greatest Hits", artist_name="Night Shift Orchestra")
    assert (await _add(lib, "the embers best", target=ember_hits)).status == "added"
    assert (await _add(lib, "night shift best", target=night_hits)).status == "added"
    assert [r.alias for r in await repo.aliases_for_target(lib, ember_hits)] == ["the embers best"]
    assert [r.alias for r in await repo.aliases_for_target(lib, night_hits)] == ["night shift best"]


@requires_db
@pytest.mark.asyncio
async def test_drawer_for_track_lists_song_artists_and_album(lib) -> None:
    song = await repo.resolve_target(lib, "track", track_id=8)
    await _add(lib, "the deep one", target=song)
    await _add(lib, "the embers", target=EMBER)
    await _mb(lib, "night crew", target_key="nightshiftorchestra", target_name="Night Shift Orchestra")
    out = await repo.drawer_for_track(lib, 8, DEV_B)
    assert out["track"]["title"] == "In Too Deep"
    assert [a["alias"] for a in out["track"]["aliases"]] == ["the deep one"]
    assert out["track"]["aliases"][0]["can_remove"] is False          # DEV_A added it
    assert out["track"]["aliases"][0]["added_by"] == "Kitchen tablet"
    assert [(a["name"], a["credit"]) for a in out["artists"]] == [("Ember Choir", False), ("Night Shift Orchestra", False)]
    assert [a["alias"] for a in out["artists"][0]["aliases"]] == ["the embers"]
    mb = out["artists"][1]["aliases"][0]
    assert (mb["alias"], mb["source"], mb["can_remove"], mb["added_by"]) == ("night crew", "musicbrainz", True, "MusicBrainz")
    assert out["album"]["name"] == "Lemonade" and out["album"]["artist_key"] == "emberchoir"
    # a band-like credit is offered whole, before its parts
    band = await repo.drawer_for_track(lib, 6, ADMIN)
    assert [(a["name"], a["credit"]) for a in band["artists"]][0] == ("Earth, Wind & Fire", True)
    loose = await repo.drawer_for_track(lib, 9, ADMIN)
    assert loose["track"]["title"] == "Bugatti" and loose["album"] is None
    assert [a["name"] for a in loose["artists"]] == ["Tech N9ne"]
    assert await repo.drawer_for_track(lib, 4242, ADMIN) is None


@requires_db
@pytest.mark.asyncio
async def test_counts_and_the_resolver_feed(lib) -> None:
    await _add(lib, "gramps")
    await _add(lib, "the embers", target=EMBER, actor=Actor(kind="voice", room_id="den"), source="voice")
    await _mb(lib, "hearth band")
    hidden = await _mb(lib, "fireside crew", suppressed=True)
    await lib.execute(text(
        "INSERT INTO library_alias_lookups (artist_key, artist_name, status, aliases_added) VALUES "
        "('hearthensemble', 'Hearth Ensemble', 'matched', 1), ('emberchoir', 'Ember Choir', 'no_match', 0)"
    ))
    await lib.commit()
    counts = await repo.alias_counts(lib)
    assert (counts["household"], counts["musicbrainz"], counts["suppressed"]) == (2, 1, 1)
    f = counts["fetch"]
    assert (f["checked"], f["matched"], f["no_match"], f["aliases_added"]) == (2, 1, 1, 1)
    assert f["artists_total"] == len(repo.lookup_candidates(_names()))
    feed = await repo.alias_rows_for_index(lib)
    assert [r["alias"] for r in feed] == ["gramps", "the embers", "hearth band"]
    assert hidden not in {r["id"] for r in feed}
    assert set(feed[0]) >= {"alias", "alias_key", "target_type", "target_key", "target_artist_key",
                            "target_track_id", "source", "created_at", "updated_at"}


@requires_db
@pytest.mark.asyncio
async def test_list_aliases_filters_and_pages(lib) -> None:
    for name in ("gramps", "the kindlers", "hearth band"):
        await _add(lib, name)
    await _add(lib, "the embers", target=EMBER)
    await _mb(lib, "fireside crew", suppressed=True)
    rows, total = await repo.list_aliases(lib, target_type="artist", target_key="hearthensemble", limit=2)
    assert total == 3 and [r.alias for r in rows] == ["gramps", "the kindlers"]
    rows, total = await repo.list_aliases(lib, include_suppressed=True)
    assert total == 5
    rows, total = await repo.list_aliases(lib, source="musicbrainz")
    assert total == 0
