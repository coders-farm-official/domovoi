"""``/api/music/aliases`` — the dashboard's "also called" door (V019).

Tiers (spoken-match contract §3): the reads are open like
``GET /api/music/library``; adding and removing wear the household device
tier (``require_device``: the device token or an admin Bearer). Inside it
the alias policy decides: an admin may remove or replace anything, a paired
device only the names IT added (the self-asserted device id from the
dashboard's cookie or ``X-Device-Id``), anyone a MusicBrainz name.

* DB-free: the route table, and the gate matrix (no credential 401, the
  dashboard cookie alone 403, the device token and an admin Bearer get
  through, a fresh install keeps its grace) with the database replaced by a
  marker — a refusal never touches it.
* On the lane's database (V019 applied): device-own-only, admin-any, the
  409 "replace it?" shape, 200 for a name that already means that, 404 for
  a target not in the library, 422 codes, the drawer's per-caller
  ``can_remove``, and the status card's snapshot fold.

Names are synthetic (checked clear of the spoken-match gate).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_app
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_route_auth_matrix import GET_ROUTES, ROUTES, _dependency_calls

BROWSER = {"X-Requested-With": "XMLHttpRequest"}
ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"
DEV_A = {HEADER: DEVICE_TOKEN, "X-Device-Id": "test-alias-dev-a"}
DEV_B = {HEADER: DEVICE_TOKEN, "X-Device-Id": "test-alias-dev-b"}
ADMIN = bearer(ADMIN_TOKEN)


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={**BROWSER, **(headers or {})}, **kw)


# ─── The route table (DB-free) ─────────────────────────────────────────────

_WEB = {(m, p): d for a, m, p, d in ROUTES if a == "web"}


@pytest.mark.parametrize(("method", "path"), [("POST", "/api/music/aliases"),
                                              ("DELETE", "/api/music/aliases/{alias_id}")])
def test_writes_are_on_the_device_tier(method: str, path: str) -> None:
    calls = list(_dependency_calls(_WEB[(method, path)]))
    assert admin_auth.require_device in calls
    assert admin_auth.require_admin_mutation not in calls


@pytest.mark.parametrize("path", ["/api/music/aliases", "/api/music/aliases/status",
                                  "/api/music/aliases/for-track/{track_id}"])
def test_reads_are_open_like_the_library(path: str) -> None:
    calls = list(_dependency_calls(getattr(GET_ROUTES[("web", path)], "dependant", None)))
    for gate in (admin_auth.require_device, admin_auth.require_device_read,
                 admin_auth.require_admin_read, admin_auth.require_admin_mutation):
        assert gate not in calls, f"GET {path} must stay open"


def test_the_openapi_spec_lists_the_routes() -> None:
    import json
    from pathlib import Path

    spec = json.loads((Path(__file__).resolve().parents[2] / "web" / "openapi.json").read_text(encoding="utf-8"))
    paths = spec["paths"]
    assert {"get", "post"} <= set(paths["/api/music/aliases"])
    assert "delete" in paths["/api/music/aliases/{alias_id}"]
    assert "get" in paths["/api/music/aliases/for-track/{track_id}"]
    assert "get" in paths["/api/music/aliases/status"]
    assert "LibraryAlias" in spec["components"]["schemas"]


# ─── The gate matrix (DB-free) ─────────────────────────────────────────────


class Reached(BaseException):
    """Raised by the database marker once a request is past the gate."""


@pytest.fixture
def marked(monkeypatch):
    from web.backend.api import music_aliases

    seen: list[str] = []

    @asynccontextmanager
    async def marker():
        seen.append("db")
        raise Reached("reached the database")
        yield  # pragma: no cover

    monkeypatch.setattr(music_aliases, "session_scope", marker)
    return seen


WRITES = [
    ("POST", "/api/music/aliases", {"alias": "gramps", "target_type": "artist", "target_name": "Hearth Ensemble"}),
    ("DELETE", "/api/music/aliases/1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=["add", "remove"])
@pytest.mark.asyncio
async def test_a_write_without_a_credential_is_refused(method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client() as c:
        r = await c.request(method, path, json=body)
    assert r.status_code == 401, r.text
    assert marked == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=["add", "remove"])
@pytest.mark.asyncio
async def test_the_dashboard_cookie_alone_cannot_write(method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client(cookies={COOKIE: ADMIN_TOKEN}) as c:
        r = await c.request(method, path, json=body)
    assert r.status_code == 403, r.text
    assert marked == []


@pytest.mark.parametrize("creds", [DEV_A, ADMIN], ids=["device-token", "admin-bearer"])
@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=["add", "remove"])
@pytest.mark.asyncio
async def test_a_paired_device_or_an_admin_gets_through(creds, method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client(creds) as c:
        with pytest.raises(Reached):
            await c.request(method, path, json=body)


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=["add", "remove"])
@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        with pytest.raises(Reached):
            await c.request(method, path, json=body)


@pytest.mark.parametrize("path", ["/api/music/aliases", "/api/music/aliases/status",
                                  "/api/music/aliases/for-track/3"])
@pytest.mark.asyncio
async def test_reads_need_no_credential(path, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client(headers={}) as c:
        with pytest.raises(Reached):
            await c.get(path)


@pytest.mark.asyncio
async def test_a_body_without_a_target_type_is_a_422_before_the_database(monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client(DEV_A) as c:
        r = await c.post("/api/music/aliases", json={"alias": "gramps"})
    assert r.status_code == 422
    assert marked == []


# ─── On the database ───────────────────────────────────────────────────────

LIBRARY = [
    (1, "/music/Hearth Ensemble/By The Hearth/01 Warm Stones.mp3", "Warm Stones", "Hearth Ensemble", "By The Hearth"),
    (2, "/music/Hearth Ensemble/By The Hearth/02 Kindling.mp3", "Kindling", "Hearth Ensemble", "By The Hearth"),
    (3, "/music/Ember Choir/Greatest Hits/01 Glow Street.mp3", "Glow Street", "Ember Choir", "Greatest Hits"),
    (4, "/music/Night Shift Orchestra/Greatest Hits/01 Coal Lane.mp3", "Coal Lane", "Night Shift Orchestra", "Greatest Hits"),
]


@pytest_asyncio.fixture
async def lib(db_session, monkeypatch):
    from domovoi.tests.library_aliases_testkit import apply_v019
    from web.backend import domovoi_client

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
    await db_session.commit()
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    monkeypatch.setattr(domovoi_client, "_snapshot", None)
    yield db_session
    await db_session.rollback()
    await db_session.execute(text("DELETE FROM devices WHERE device_id LIKE 'test-alias-dev-%'"))
    await db_session.commit()


async def _post(headers: dict[str, str], body: dict[str, Any]):
    async with _client(headers) as c:
        return await c.post("/api/music/aliases", json=body)


def _artist(alias: str, name: str = "Hearth Ensemble", **kw) -> dict[str, Any]:
    return {"alias": alias, "target_type": "artist", "target_name": name, **kw}


@requires_db
@pytest.mark.asyncio
async def test_add_list_and_one_to_many(lib) -> None:
    for name in ("gramps", "the kindlers", "hearth band"):
        r = await _post(DEV_A, _artist(name))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "added" and body["shadows"] == []
        a = body["alias"]
        assert (a["target_type"], a["target_key"], a["target_name"], a["source"]) == (
            "artist", "hearthensemble", "Hearth Ensemble", "manual")
        assert a["added_by"] == "Kitchen tablet" and a["can_remove"] is True
        assert "test-alias-dev-a" not in r.text
    async with _client(headers={}) as c:
        page = (await c.get("/api/music/aliases", params={"target_type": "artist",
                                                            "target_key": "hearthensemble"})).json()
    assert page["total"] == 3
    assert [i["alias"] for i in page["items"]] == ["gramps", "the kindlers", "hearth band"]
    assert all(i["can_remove"] is False for i in page["items"])      # an anonymous reader


@requires_db
@pytest.mark.asyncio
async def test_same_target_is_200_and_another_target_is_a_409_question(lib) -> None:
    first = (await _post(DEV_A, _artist("gramps"))).json()["alias"]
    again = await _post(DEV_B, _artist("Gramps!"))
    assert again.status_code == 200 and again.json()["status"] == "exists"
    assert again.json()["alias"]["id"] == first["id"]

    taken = await _post(DEV_B, _artist("gramps", "Ember Choir"))
    assert taken.status_code == 409
    detail = taken.json()["detail"]
    assert detail["code"] == "alias_taken" and detail["can_replace"] is False
    assert detail["existing"]["id"] == first["id"] and detail["existing"]["target_name"] == "Hearth Ensemble"
    assert detail["message"] == "'gramps' already means Hearth Ensemble."
    # DEV_B may not replace it, even when asking
    refused = await _post(DEV_B, _artist("gramps", "Ember Choir", replace=True))
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only an admin can change a name someone else added."
    # the device that added it may
    mine = await _post(DEV_A, _artist("gramps", "Ember Choir"))
    assert mine.status_code == 409 and mine.json()["detail"]["can_replace"] is True
    moved = await _post(DEV_A, _artist("gramps", "Ember Choir", replace=True))
    assert moved.status_code == 201 and moved.json()["status"] == "replaced"
    assert moved.json()["alias"]["id"] == first["id"] and moved.json()["alias"]["target_key"] == "emberchoir"


@requires_db
@pytest.mark.asyncio
async def test_admin_replaces_and_removes_anyones(lib) -> None:
    first = (await _post(DEV_A, _artist("gramps"))).json()["alias"]
    r = await _post(ADMIN, _artist("gramps", "Ember Choir", replace=True))
    assert r.status_code == 201 and r.json()["alias"]["added_by"] == "admin"
    second = (await _post(DEV_A, _artist("warmies"))).json()["alias"]
    async with _client(ADMIN) as c:
        assert (await c.delete(f"/api/music/aliases/{second['id']}")).status_code == 204
        assert (await c.delete(f"/api/music/aliases/{second['id']}")).status_code == 404
        assert (await c.delete(f"/api/music/aliases/{first['id']}")).status_code == 204


@requires_db
@pytest.mark.asyncio
async def test_a_device_removes_only_its_own(lib) -> None:
    mine = (await _post(DEV_A, _artist("gramps"))).json()["alias"]
    anonymous = (await _post({HEADER: DEVICE_TOKEN}, _artist("warmies"))).json()["alias"]
    assert anonymous["added_by"] == "a device" and anonymous["can_remove"] is False
    async with _client(DEV_B) as c:
        r = await c.delete(f"/api/music/aliases/{mine['id']}")
        assert r.status_code == 403
        assert r.json()["detail"] == "Only an admin can remove a name someone else added."
    async with _client(DEV_A) as c:
        assert (await c.delete(f"/api/music/aliases/{anonymous['id']}")).status_code == 403
        assert (await c.delete(f"/api/music/aliases/{mine['id']}")).status_code == 204


@requires_db
@pytest.mark.asyncio
async def test_a_musicbrainz_name_anyone_may_hide(lib) -> None:
    row = (await lib.execute(text(
        "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, target_name, source, "
        "created_by_kind) VALUES ('night crew', 'nightcrew', 'artist', 'nightshiftorchestra', "
        "'Night Shift Orchestra', 'musicbrainz', 'system') RETURNING id"
    ))).first()
    await lib.commit()
    async with _client(DEV_B) as c:
        assert (await c.delete(f"/api/music/aliases/{row[0]}")).status_code == 204
    hidden = (await lib.execute(text("SELECT suppressed FROM library_aliases WHERE id = :i"), {"i": row[0]})).scalar_one()
    assert hidden is True
    async with _client(headers={}) as c:
        assert (await c.get("/api/music/aliases")).json()["total"] == 0
        assert (await c.get("/api/music/aliases", params={"include_suppressed": "true"})).json()["total"] == 1


@requires_db
@pytest.mark.asyncio
async def test_targets_must_be_in_the_library_and_names_must_be_usable(lib) -> None:
    r = await _post(DEV_A, _artist("gramps", "Nobody Here"))
    assert r.status_code == 404 and "Nobody Here" in r.json()["detail"]
    r = await _post(DEV_A, {"alias": "x", "target_type": "track", "target_track_id": 4242})
    assert r.status_code == 404
    r = await _post(DEV_A, _artist("hearth-ensemble"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "already_its_name"
    r = await _post(DEV_A, _artist("~ ~ -"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "nothing_to_say"
    r = await _post(DEV_A, _artist("y" * 121))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_long"


@requires_db
@pytest.mark.asyncio
async def test_albums_songs_and_shadows(lib) -> None:
    r = await _post(DEV_A, {"alias": "night shift best", "target_type": "album", "target_name": "Greatest Hits",
                            "target_artist_name": "Night Shift Orchestra"})
    assert r.status_code == 201
    a = r.json()["alias"]
    assert (a["target_type"], a["target_key"], a["target_artist_key"]) == ("album", "greatesthits", "nightshiftorchestra")
    r = await _post(DEV_A, {"alias": "the warm one", "target_type": "track", "target_track_id": 1})
    assert r.status_code == 201
    assert (r.json()["alias"]["target_track_id"], r.json()["alias"]["target_name"]) == (1, "Warm Stones")
    # a name that is another library name's: allowed, and the answer says which
    r = await _post(DEV_A, _artist("kindling", "Ember Choir"))
    assert r.status_code == 201 and r.json()["shadows"] == [{"type": "title", "name": "Kindling"}]


@requires_db
@pytest.mark.asyncio
async def test_the_drawer_call_marks_what_this_caller_may_remove(lib) -> None:
    await _post(DEV_A, _artist("gramps"))
    await _post(DEV_A, {"alias": "the warm one", "target_type": "track", "target_track_id": 1})
    async with _client(DEV_A) as c:
        mine = (await c.get("/api/music/aliases/for-track/1")).json()
    async with _client(DEV_B) as c:
        theirs = (await c.get("/api/music/aliases/for-track/1")).json()
    assert mine["track"]["aliases"][0]["can_remove"] is True
    assert theirs["track"]["aliases"][0]["can_remove"] is False
    assert [a["name"] for a in mine["artists"]] == ["Hearth Ensemble"]
    assert [x["alias"] for x in mine["artists"][0]["aliases"]] == ["gramps"]
    assert mine["album"]["name"] == "By The Hearth" and mine["album"]["artist_name"] == "Hearth Ensemble"
    async with _client(headers={}) as c:
        assert (await c.get("/api/music/aliases/for-track/4242")).status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_status_folds_the_cores_snapshot(lib, monkeypatch) -> None:
    from web.backend import domovoi_client

    await _post(DEV_A, _artist("gramps"))
    async with _client(headers={}) as c:
        unknown = (await c.get("/api/music/aliases/status")).json()
    assert unknown["household"] == 1 and unknown["musicbrainz"] == 0
    assert unknown["fetch"]["enabled"] is None and unknown["fetch"]["state"] == "unknown"
    assert unknown["fetch"]["artists_total"] == 3
    monkeypatch.setattr(domovoi_client, "_snapshot", {"music_alias_fetch": {
        "enabled": True, "state": "running", "last_error": None}})
    async with _client(headers={}) as c:
        live = (await c.get("/api/music/aliases/status")).json()
    assert (live["fetch"]["enabled"], live["fetch"]["state"]) == (True, "running")
