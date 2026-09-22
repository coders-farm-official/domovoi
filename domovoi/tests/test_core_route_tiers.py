"""Which tier each core route sits on, and what it answers without a
credential (CORE-4 / ADD-1 / ADD-3).

The core used to answer every one of these to anyone who could reach port
6370 once setup was done. Now:

* the **device tier** (``require_device``) covers the ordinary household
  actions — a turn, an announcement, playback, the room queue — and takes
  the household ``X-Device-Token`` or an admin Bearer;
* the **admin tier** (``require_admin_mutation``) covers the
  code-adjacent and physical-effect ones — the git pull, a Pi restart or
  config rewrite, wake recording/push, clip re-renders, library sweeps —
  and takes an admin Bearer;
* the satellite log pull is an admin READ (``require_admin_read``): the
  ring holds transcripts of what the room said;
* the chat agent's callback takes its own per-boot secret, because
  Letta's sandbox can hold neither credential.

Almost everything here is DB-FREE: the structural half reads the route
table, and the behavioural half drives the real core app with the auth
primitives faked (``install_fake_db``), because a gate answers before the
handler ever reaches Postgres. One ``requires_db`` test at the end walks
the same ground against real rows, so the fakes can't be the only thing
the matrix agrees with.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi.db.session import session_scope
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    web_client,
)
from domovoi.tests.conftest import requires_db

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"

# ─── The tier map: route → the gate it must wear ──────────────────────────

DEVICE_TIER = [
    ("POST", "/v1/intent"),
    ("POST", "/v1/admin/announce"),
    ("POST", "/v1/admin/dropin/end"),
    ("POST", "/v1/admin/satellite/set-volume"),
    ("POST", "/v1/admin/satellites/{room_id}/label"),
    ("POST", "/v1/admin/version/check"),
    ("POST", "/v1/admin/voices/sample"),
    ("POST", "/v1/admin/music/play"),
    ("POST", "/v1/admin/music/play-track"),
    ("POST", "/v1/admin/music/play-tracks"),
    ("POST", "/v1/admin/music/play-playlist"),
    ("POST", "/v1/admin/music/add-by-query"),
    ("POST", "/v1/admin/music/{action}/{room_id}"),
    # ADD-3: the queue routes the device blocks are about.
    ("POST", "/v1/admin/music/queue/{room_id}/add"),
    ("POST", "/v1/admin/music/queue/{room_id}/remove"),
    ("POST", "/v1/admin/music/queue/{room_id}/move"),
    ("POST", "/v1/admin/music/queue/{room_id}/clear"),
]

ADMIN_TIER = [
    ("POST", "/v1/admin/version/pull"),
    # CORE-2: opening a live two-way mic bridge between two rooms from
    # an HTTP call, with nobody in either room asked first, is a
    # physical-effect action - admin Bearer, not the household token.
    ("POST", "/v1/admin/dropin/start"),
    ("POST", "/v1/admin/satellite/restart"),
    ("POST", "/v1/admin/satellite/display"),
    ("POST", "/v1/admin/satellite/{room_id}/config"),
    ("POST", "/v1/admin/wake/record/start"),
    ("POST", "/v1/admin/wake/record/stop"),
    ("POST", "/v1/admin/wake/push"),
    ("POST", "/v1/admin/wake/score"),
    ("POST", "/v1/admin/sounds/regenerate"),
    ("POST", "/v1/admin/sounds/setup-clips"),
    ("POST", "/v1/admin/library/reindex"),
    ("POST", "/v1/admin/library/enrich"),
]

# ADD-1 — an admin READ: the dashboard cookie may render it, nothing less.
ADMIN_READ_TIER = [
    ("GET", "/v1/admin/satellite/{room_id}/logs"),
]


def _dependency_calls(dependant: Any) -> Iterator[Any]:
    if dependant is None:
        return
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            yield dep.call
        yield from _dependency_calls(dep)


def _gates_for(method: str, path: str) -> list[Any]:
    try:
        from fastapi.routing import iter_route_contexts

        contexts = list(iter_route_contexts(core_app.routes))
    except ImportError:  # pragma: no cover — older FastAPI flattens itself
        from fastapi.routing import APIRoute

        contexts = [r for r in core_app.routes if isinstance(r, APIRoute)]
    for rc in contexts:
        if getattr(rc, "path", None) == path and method in (
            getattr(rc, "methods", None) or set()
        ):
            return list(_dependency_calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route on the core app")


@pytest.mark.parametrize(
    ("method", "path"), DEVICE_TIER, ids=[f"{m} {p}" for m, p in DEVICE_TIER]
)
def test_ordinary_actions_are_on_the_device_tier(method, path) -> None:
    """A household action takes the device token or an admin Bearer —
    never the dashboard cookie alone, and never nothing."""
    gates = _gates_for(method, path)
    assert admin_auth.require_device in gates
    assert admin_auth.require_admin_mutation not in gates


@pytest.mark.parametrize(
    ("method", "path"), ADMIN_TIER, ids=[f"{m} {p}" for m, p in ADMIN_TIER]
)
def test_code_adjacent_actions_are_on_the_admin_tier(method, path) -> None:
    """Changing what the host runs, what a Pi runs, or what the library
    sweeps takes an admin Bearer."""
    gates = _gates_for(method, path)
    assert admin_auth.require_admin_mutation in gates
    assert admin_auth.require_device not in gates


@pytest.mark.parametrize(
    ("method", "path"), ADMIN_READ_TIER, ids=[f"{m} {p}" for m, p in ADMIN_READ_TIER]
)
def test_satellite_logs_are_an_admin_read(method, path) -> None:
    """ADD-1: the log ring is a room transcript, so the route that holds
    it wears the same gate the dashboard hop always had."""
    assert admin_auth.require_admin_read in _gates_for(method, path)


def test_the_chat_callback_wears_its_own_secret_gate() -> None:
    gates = _gates_for("POST", "/v1/admin/chat-tool")
    assert admin_auth.require_chat_callback in gates
    assert admin_auth.require_admin_mutation not in gates
    assert admin_auth.require_device not in gates


# ─── Behaviour: what an uncredentialed caller actually gets ───────────────


def _client() -> AsyncClient:
    # raise_app_exceptions=False: a request that PASSES the gate goes on to
    # a handler that wants Postgres/MPD and fails — which is exactly the
    # proof we want ("the gate let it through"), not an error to chase.
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _sample_path(path: str) -> str:
    return path.replace("{room_id}", "kitchen").replace("{action}", "pause")


# A body each route accepts, so a refusal is the gate's and not pydantic's.
BODIES: dict[str, dict[str, Any]] = {
    "/v1/intent": {"transcript": "what time is it", "room_id": "kitchen"},
    "/v1/admin/announce": {"room_id": "kitchen", "message": "hello"},
    "/v1/admin/dropin/start": {"initiator_room": "kitchen", "target_room": "office"},
    "/v1/admin/dropin/end": {"room_id": "kitchen"},
    "/v1/admin/satellite/set-volume": {"room_id": "kitchen", "level": 30},
    "/v1/admin/satellite/restart": {"room_id": "kitchen"},
    "/v1/admin/satellite/display": {"room_id": "kitchen", "action": "on"},
    "/v1/admin/satellite/{room_id}/config": {"changes": {"mic_gain": 1}},
    "/v1/admin/satellites/{room_id}/label": {"room_label": "Kitchen"},
    "/v1/admin/voices/sample": {"name": "aria"},
    "/v1/admin/wake/record/start": {"room_id": "kitchen", "wake_word_id": 1},
    "/v1/admin/wake/record/stop": {"room_id": "kitchen"},
    "/v1/admin/wake/push": {"room_id": "kitchen", "wake_word_id": 1},
    "/v1/admin/wake/score": {"wake_word_id": 1},
    "/v1/admin/music/play": {"room_id": "kitchen", "query": "jazz"},
    "/v1/admin/music/play-track": {"room_id": "kitchen", "track_id": 1},
    "/v1/admin/music/play-tracks": {"room_id": "kitchen", "track_ids": [1]},
    "/v1/admin/music/play-playlist": {"room_id": "kitchen", "playlist_id": 1},
    "/v1/admin/music/add-by-query": {"room_id": "kitchen", "query": "jazz"},
    "/v1/admin/music/queue/{room_id}/add": {"track_ids": [1]},
    "/v1/admin/music/queue/{room_id}/remove": {"song_ids": [1]},
    "/v1/admin/music/queue/{room_id}/move": {"song_id": 1, "to_position": 0},
}

GATED_POSTS = DEVICE_TIER + ADMIN_TIER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), GATED_POSTS, ids=[f"{m} {p}" for m, p in GATED_POSTS]
)
async def test_gated_route_refuses_a_caller_with_no_credential(
    method, path, monkeypatch
) -> None:
    """Post-setup, reaching the port is not enough for any of them."""
    install_fake_db(
        monkeypatch,
        admin=True,
        sessions={ADMIN_TOKEN},
        device_token=DEVICE_TOKEN,
    )
    async with _client() as c:
        r = await c.post(_sample_path(path), json=BODIES.get(path, {}))
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), GATED_POSTS, ids=[f"{m} {p}" for m, p in GATED_POSTS]
)
async def test_gated_route_lets_an_admin_bearer_through(
    method, path, monkeypatch
) -> None:
    """An admin Bearer satisfies both tiers — whatever the handler then
    makes of a test box with no database, it is not a refusal."""
    install_fake_db(
        monkeypatch,
        admin=True,
        sessions={ADMIN_TOKEN},
        device_token=DEVICE_TOKEN,
    )
    async with _client() as c:
        r = await c.post(
            _sample_path(path), json=BODIES.get(path, {}), headers=bearer(ADMIN_TOKEN)
        )
    assert r.status_code not in (401, 403), r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), DEVICE_TIER, ids=[f"{m} {p}" for m, p in DEVICE_TIER]
)
async def test_device_tier_route_accepts_the_household_token(
    method, path, monkeypatch
) -> None:
    """A satellite or phone holding the household token gets in without
    anyone signing into the dashboard."""
    install_fake_db(
        monkeypatch,
        admin=True,
        sessions={ADMIN_TOKEN},
        device_token=DEVICE_TOKEN,
    )
    async with _client() as c:
        r = await c.post(
            _sample_path(path),
            json=BODIES.get(path, {}),
            headers={HEADER: DEVICE_TOKEN},
        )
    assert r.status_code not in (401, 403), r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), ADMIN_TIER, ids=[f"{m} {p}" for m, p in ADMIN_TIER]
)
async def test_admin_tier_route_refuses_the_household_token(
    method, path, monkeypatch
) -> None:
    """The device token is a household credential, not an admin one: it
    must not restart a Pi or pull code."""
    install_fake_db(
        monkeypatch,
        admin=True,
        sessions={ADMIN_TOKEN},
        device_token=DEVICE_TOKEN,
    )
    async with _client() as c:
        r = await c.post(
            _sample_path(path),
            json=BODIES.get(path, {}),
            headers={HEADER: DEVICE_TOKEN},
        )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_the_whole_gated_surface_still_answers_before_setup(monkeypatch) -> None:
    """A fresh install has no credential to present yet, so the daily
    surface keeps its LAN grace — a throwaway instance still boots and
    works before anyone claims admin."""
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        for _method, path in GATED_POSTS:
            r = await c.post(_sample_path(path), json=BODIES.get(path, {}))
            assert r.status_code not in (401, 403), f"{path}: {r.status_code}"


@pytest.mark.asyncio
async def test_satellite_log_pull_refuses_a_caller_with_no_session(
    monkeypatch,
) -> None:
    """ADD-1, from the outside: the transcript ring is not readable by
    anything that merely reached the port; the dashboard cookie is."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        assert (await c.get("/v1/admin/satellite/kitchen/logs")).status_code == 401
        assert (
            await c.get(
                "/v1/admin/satellite/kitchen/logs", headers=bearer(ADMIN_TOKEN)
            )
        ).status_code != 401
    async with _client() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        assert (await c.get("/v1/admin/satellite/kitchen/logs")).status_code != 401


# ─── The chat agent's callback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_tool_refuses_a_caller_without_the_boot_secret(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    body = {"tool": "music", "args": {}}
    async with _client() as c:
        assert (await c.post("/v1/admin/chat-tool", json=body)).status_code == 401
        # An admin session is not the credential this endpoint wants.
        r = await c.post("/v1/admin/chat-tool", json=body, headers=bearer(ADMIN_TOKEN))
        assert r.status_code == 401
        r = await c.post(
            "/v1/admin/chat-tool",
            json=body,
            headers={admin_auth.CHAT_CALLBACK_HEADER: "not-the-secret"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_tool_accepts_this_boot_secret(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True)
    async with _client() as c:
        r = await c.post(
            "/v1/admin/chat-tool",
            json={"tool": "nonexistent_handler", "args": {}},
            headers={
                admin_auth.CHAT_CALLBACK_HEADER: admin_auth.chat_callback_secret()
            },
        )
    assert r.status_code == 200
    # dispatch_tool degrades to an apology rather than raising — the point
    # here is only that the gate let the call through.
    assert "text" in r.json()


def test_the_boot_secret_is_stable_within_a_boot_and_changes_on_a_re_key() -> None:
    first = admin_auth.chat_callback_secret()
    assert admin_auth.chat_callback_secret() == first
    second = admin_auth.reset_chat_callback_secret()
    assert second != first
    assert admin_auth.chat_callback_secret() == second


def test_the_generated_proxy_tool_carries_the_boot_secret() -> None:
    """The proxy source Letta runs in its sandbox is the only thing that
    holds the secret, and it is written in at generation time."""
    from domovoi.letta_tools import _proxy_source

    admin_auth.reset_chat_callback_secret()
    source = _proxy_source(
        {
            "name": "music",
            "description": "Play music.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "what"}},
                "required": ["query"],
            },
        },
        "http://host.docker.internal:6370",
    )
    assert admin_auth.CHAT_CALLBACK_HEADER in source
    assert admin_auth.chat_callback_secret() in source
    assert "/v1/admin/chat-tool" in source
    compile(source, "<letta-proxy-tool>", "exec")


# ─── The same matrix against the real tables ──────────────────────────────


@pytest.mark.asyncio
@requires_db
async def test_the_tiers_hold_against_the_real_tables(_db) -> None:
    """The DB-free matrix fakes the auth primitives, so this walks the
    same ground once with real rows: a claimed admin, the household token
    the claim rotated into ``household_device_tokens``, and two live
    routes — one per tier."""
    async with web_client() as web:
        admin_token = await claim_admin(web)
    device_token = await db_device_token()
    assert device_token, "first-run setup should have left a household token"

    async with _client() as core:
        # Device tier: the household token acts, the admin Bearer acts,
        # nothing acts with neither. (503 = "no satellites connected",
        # which is the handler talking — the gate let it through.)
        body = {"room_id": "kitchen", "message": "dinner"}
        assert (await core.post("/v1/admin/announce", json=body)).status_code == 401
        r = await core.post(
            "/v1/admin/announce", json=body, headers={HEADER: device_token}
        )
        assert r.status_code == 503, r.text
        r = await core.post(
            "/v1/admin/announce", json=body, headers=bearer(admin_token)
        )
        assert r.status_code == 503, r.text

        # Admin tier: the household token is not an admin credential.
        restart = {"room_id": "kitchen"}
        assert (
            await core.post("/v1/admin/satellite/restart", json=restart)
        ).status_code == 401
        r = await core.post(
            "/v1/admin/satellite/restart", json=restart, headers={HEADER: device_token}
        )
        assert r.status_code == 401, r.text
        r = await core.post(
            "/v1/admin/satellite/restart", json=restart, headers=bearer(admin_token)
        )
        assert r.status_code == 503, r.text

        # A token from before a rotation stops working immediately.
        async with session_scope() as s:
            rotated = await admin_auth.rotate_device_token(s)
        assert rotated != device_token
        r = await core.post(
            "/v1/admin/announce", json=body, headers={HEADER: device_token}
        )
        assert r.status_code == 401, r.text
        r = await core.post(
            "/v1/admin/announce", json=body, headers={HEADER: rotated}
        )
        assert r.status_code == 503, r.text
