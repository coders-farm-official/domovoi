"""``/api/lists`` — the household's lists for the dashboard and the apps (V048).

Tiers, like the calendar: reads take ``require_device_read`` (the household
token, an admin Bearer, the dashboard cookie, the pre-setup grace); writes
take ``require_device`` (the household token or an admin Bearer).

* DB-free: the route table, and the gate matrix with the database replaced
  by a marker (a refusal never reaches it).
* On the lane's database: make / rename / delete lists, the one-name rule
  ("Groceries" beside "Shopping" is a 409 ``list_name_taken``), add with
  its outcomes, cross off and back, edit, reorder, clear the crossed-off
  items, 404s, and the realtime digest moving on every write.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

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
DEVICE = {HEADER: DEVICE_TOKEN}

WRITES = [
    ("POST", "/api/lists", {"name": "Camping"}),
    ("PATCH", "/api/lists/{list_id}", {"name": "Camping"}),
    ("DELETE", "/api/lists/{list_id}", None),
    ("POST", "/api/lists/{list_id}/items", {"text": "Tents"}),
    ("PATCH", "/api/lists/{list_id}/items/{item_id}", {"done": True}),
    ("DELETE", "/api/lists/{list_id}/items/{item_id}", None),
    ("PUT", "/api/lists/{list_id}/items/order", {"item_ids": [1]}),
    ("POST", "/api/lists/{list_id}/clear-done", None),
]
READS = ["/api/lists", "/api/lists/{list_id}"]


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={**BROWSER, **(headers or {})}, **kw)


def _url(path: str) -> str:
    return path.replace("{list_id}", "1").replace("{item_id}", "1")


# ─── The route table (DB-free) ─────────────────────────────────────────────

_WEB = {(m, p): d for a, m, p, d in ROUTES if a == "web"}


@pytest.mark.parametrize(("method", "path", "_body"), WRITES)
def test_writes_are_on_the_device_tier(method: str, path: str, _body) -> None:
    calls = list(_dependency_calls(_WEB[(method, path)]))
    assert admin_auth.require_device in calls
    assert admin_auth.require_admin_mutation not in calls


@pytest.mark.parametrize("path", READS)
def test_reads_need_a_paired_device(path: str) -> None:
    calls = list(_dependency_calls(getattr(GET_ROUTES[("web", path)], "dependant", None)))
    assert admin_auth.require_device_read in calls


def test_the_openapi_spec_lists_the_routes() -> None:
    spec = json.loads((Path(__file__).resolve().parents[2] / "web" / "openapi.json").read_text(encoding="utf-8"))
    paths = spec["paths"]
    assert {"get", "post"} <= set(paths["/api/lists"])
    assert {"get", "patch", "delete"} <= set(paths["/api/lists/{list_id}"])
    assert "post" in paths["/api/lists/{list_id}/items"]
    assert {"patch", "delete"} <= set(paths["/api/lists/{list_id}/items/{item_id}"])
    assert "put" in paths["/api/lists/{list_id}/items/order"]
    assert "post" in paths["/api/lists/{list_id}/clear-done"]


def test_lists_ride_the_realtime_socket() -> None:
    from web.backend import realtime

    assert realtime.NOTIFY_CHANNEL_TO_REALTIME["lists_changed"] == "lists"
    assert "lists" in realtime.StatePollLoop._CHANNEL_HELPERS
    assert "lists" in realtime.CORE_REALTIME_CHANNELS


# ─── The gate matrix (DB-free: the session is a marker) ────────────────────


class _Reached(Exception):
    pass


@pytest.fixture
def marked(monkeypatch):
    from web.backend.api import lists as lists_api

    @asynccontextmanager
    async def marker():
        raise _Reached()
        yield  # pragma: no cover

    monkeypatch.setattr(lists_api, "session_scope", marker)


async def _send(method: str, path: str, body, headers=None, **kw):
    async with _client(headers, **kw) as c:
        try:
            r = await c.request(method, _url(path), json=body)
        except _Reached:
            return "reached"
    return r.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_a_write_without_a_credential_is_refused(method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    assert await _send(method, path, body) == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_the_dashboard_cookie_alone_cannot_write(method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    assert await _send(method, path, body, cookies={COOKIE: ADMIN_TOKEN}) in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("creds", [DEVICE, bearer(ADMIN_TOKEN)], ids=["device", "admin"])
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_a_paired_device_or_an_admin_gets_through(creds, method, path, body, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    assert await _send(method, path, body, creds) == "reached"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", READS)
async def test_a_read_needs_a_household_credential(path, monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    assert await _send("GET", path, None) == 401
    assert await _send("GET", path, None, DEVICE) == "reached"


@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(monkeypatch, marked) -> None:
    install_fake_db(monkeypatch, admin=False)
    assert await _send("POST", "/api/lists/{list_id}/items", {"text": "Tents"}) == "reached"
    assert await _send("GET", "/api/lists", None) == "reached"


# ─── On the lane's database ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def house(db_session, monkeypatch):
    await db_session.execute(text(
        "INSERT INTO lists (name, name_key, position) VALUES ('To-do', 'todo', 1), ('Shopping', 'shopping', 2)"
    ))
    await db_session.commit()
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    yield db_session


async def _call(method: str, path: str, body=None):
    async with _client(DEVICE) as c:
        return await c.request(method, path, json=body)


async def _ids(name: str) -> int:
    r = await _call("GET", "/api/lists")
    return next(x["id"] for x in r.json() if x["name"] == name)


@requires_db
@pytest.mark.asyncio
async def test_lists_made_renamed_and_deleted(house) -> None:
    r = await _call("GET", "/api/lists")
    assert r.status_code == 200
    assert [(x["name"], x["open_count"], x["done_count"]) for x in r.json()] == [
        ("To-do", 0, 0), ("Shopping", 0, 0)]
    r = await _call("POST", "/api/lists", {"name": "Camping list"})
    assert r.status_code == 201 and r.json()["name"] == "Camping"
    camping = r.json()["id"]
    r = await _call("POST", "/api/lists", {"name": "Groceries"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "list_name_taken"
    assert "Shopping" in r.json()["detail"]["message"]
    r = await _call("PATCH", f"/api/lists/{camping}", {"name": "To do"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "list_name_taken"
    r = await _call("PATCH", f"/api/lists/{camping}", {"name": "Camping trip"})
    assert r.status_code == 200 and r.json()["name"] == "Camping trip"
    r = await _call("PATCH", f"/api/lists/{camping}", {"name": "   "})
    assert r.status_code == 422
    r = await _call("DELETE", f"/api/lists/{camping}")
    assert r.status_code == 204
    r = await _call("DELETE", f"/api/lists/{camping}")
    assert r.status_code == 404
    r = await _call("GET", f"/api/lists/{camping}")
    assert r.status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_items_added_crossed_off_edited_ordered_cleared(house) -> None:
    shop = await _ids("Shopping")
    base = f"/api/lists/{shop}/items"
    r = await _call("POST", base, {"text": "  Milk  "})
    assert r.status_code == 201 and r.json()["outcome"] == "added"
    milk = r.json()["item"]
    assert (milk["text"], milk["done"], milk["added_via"], milk["added_by_room"]) == ("Milk", False, "screen", None)
    r = await _call("POST", base, {"text": "milk"})
    assert r.json()["outcome"] == "already" and r.json()["item"]["id"] == milk["id"]
    eggs = (await _call("POST", base, {"text": "Eggs"})).json()["item"]
    bread = (await _call("POST", base, {"text": "Bread"})).json()["item"]

    r = await _call("PATCH", f"{base}/{milk['id']}", {"done": True})
    assert r.status_code == 200 and r.json()["done"] and r.json()["done_at"]
    r = await _call("POST", base, {"text": "Milk"})
    assert r.json()["outcome"] == "reopened" and not r.json()["item"]["done"]
    r = await _call("PATCH", f"{base}/{eggs['id']}", {"text": "Free-range eggs"})
    assert r.json()["text"] == "Free-range eggs"
    r = await _call("PATCH", f"{base}/{eggs['id']}", {})
    assert r.status_code == 400

    r = await _call("PUT", f"{base}/order", {"item_ids": [bread["id"], eggs["id"]]})
    assert r.status_code == 200
    assert [i["text"] for i in r.json()] == ["Bread", "Free-range eggs", "Milk"]
    r = await _call("PUT", f"{base}/order", {"item_ids": [999999]})
    assert r.status_code == 400

    await _call("PATCH", f"{base}/{bread['id']}", {"done": True})
    r = await _call("GET", f"/api/lists/{shop}")
    assert r.json()["list"]["open_count"] == 2 and r.json()["list"]["done_count"] == 1
    r = await _call("POST", f"/api/lists/{shop}/clear-done")
    assert r.json() == {"removed": 1}
    r = await _call("DELETE", f"{base}/{milk['id']}")
    assert r.status_code == 204
    r = await _call("GET", f"/api/lists/{shop}")
    assert [i["text"] for i in r.json()["items"]] == ["Free-range eggs"]
    # Another list's id with this item: not found.
    todo = await _ids("To-do")
    r = await _call("PATCH", f"/api/lists/{todo}/items/{eggs['id']}", {"done": True})
    assert r.status_code == 404
    r = await _call("POST", "/api/lists/999999/items", {"text": "x"})
    assert r.status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_a_voice_add_shows_who_and_where(house) -> None:
    from domovoi import lists as L

    alex = (await house.execute(text("INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one()
    todo = await L.find_list(house, "to-do")
    await L.add_item(house, todo.id, "Tarp the trailer", person_id=alex, room="garage", via="voice")
    await house.commit()
    r = await _call("GET", f"/api/lists/{todo.id}")
    [item] = r.json()["items"]
    assert (item["added_via"], item["added_by_name"], item["added_by_room"]) == ("voice", "Alex", "garage")


@requires_db
@pytest.mark.asyncio
async def test_the_realtime_digest_moves_on_every_write_and_names_nothing(house) -> None:
    from web.backend.api import lists as lists_api

    shop = await _ids("Shopping")
    seen = [await lists_api.snapshot()]
    milk = (await _call("POST", f"/api/lists/{shop}/items", {"text": "Milk"})).json()["item"]
    seen.append(await lists_api.snapshot())
    await _call("PATCH", f"/api/lists/{shop}/items/{milk['id']}", {"done": True})
    seen.append(await lists_api.snapshot())
    await _call("DELETE", f"/api/lists/{shop}/items/{milk['id']}")
    seen.append(await lists_api.snapshot())
    await _call("PATCH", f"/api/lists/{shop}", {"name": "Groceries and more"})
    seen.append(await lists_api.snapshot())
    assert all(a != b for a, b in zip(seen, seen[1:])), seen
    assert "Milk" not in json.dumps(seen) and "Groceries" not in json.dumps(seen)
