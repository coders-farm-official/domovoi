"""Which tier each core route sits on, and what it answers without a
credential (CORE-4 / ADD-1 / ADD-3).

The core used to answer every one of these to anyone who could reach port
6370 once setup was done. Now:

* the **device tier** (``require_device``) covers the ordinary household
  actions — a turn, an announcement, playback, the room queue — and takes
  the household ``X-Device-Token`` or an admin Bearer. The transport row
  and the queue's add / remove / move also take a GUEST on a playing room
  (owner decisions 2026-10-10, ``domovoi/guest_queue.py``), so they wear
  that gate, with the device tier inside it (:data:`GUEST_TIER`);
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
    # F-A017: the MPD half of a post-write refresh, on its own. No caller
    # input, no library sweep — the sweep below stays admin.
    ("POST", "/v1/admin/music/mpd-rescan"),
    # ADD-3: the queue route the device blocks are about. Clear stops the
    # room, so it is never a guest's.
    ("POST", "/v1/admin/music/queue/{room_id}/clear"),
    # Groups (V060): making and changing named groups, and pulling music
    # into other rooms, are the household's — never a guest's (CORE-11).
    ("POST", "/v1/admin/music/groups"),
    ("PATCH", "/v1/admin/music/groups/{group_id}"),
    ("DELETE", "/v1/admin/music/groups/{group_id}"),
    ("POST", "/v1/admin/music/shared"),
    ("POST", "/v1/admin/music/shared/join"),
    ("POST", "/v1/admin/music/shared/leave"),
    ("POST", "/v1/admin/music/shared/{share_id}/end"),
]

# The device tier AND a guest on a playing room (owner decisions
# 2026-10-10, domovoi/guest_queue.py): the queue edits beside clear and the
# transport row (whose stop stays the household's inside the gate).
# test_guest_queue.py pins what a guest gets; here they hold the device
# tier's half — a household token or an admin Bearer, never the cookie.
GUEST_TIER = [
    ("POST", "/v1/admin/music/{action}/{room_id}"),
    ("POST", "/v1/admin/music/queue/{room_id}/add"),
    ("POST", "/v1/admin/music/queue/{room_id}/remove"),
    ("POST", "/v1/admin/music/queue/{room_id}/move"),
]

ADMIN_TIER = [
    ("POST", "/v1/admin/satellite/restart"),
    ("POST", "/v1/admin/satellite/display"),
    ("POST", "/v1/admin/satellite/{room_id}/voice"),
    # Read-only diagnostics (1.5.0): the recent-logs probe carries what the
    # room heard, so admin at both hops, like the log pull.
    ("POST", "/v1/admin/satellite/{room_id}/diagnostics"),
    ("POST", "/v1/admin/satellite/{room_id}/diagnostics/{probe}"),
    # XVF3800 DSP presets and test markers (1.4.1, domovoi/mic_dsp.py).
    ("POST", "/v1/admin/satellite/{room_id}/mic_dsp"),
    ("POST", "/v1/admin/satellite/{room_id}/mic_dsp/mark"),
    ("POST", "/v1/admin/wake/record/stop"),
    ("POST", "/v1/admin/wake/push"),
    ("POST", "/v1/admin/wake/score"),
    ("POST", "/v1/admin/sounds/regenerate"),
    ("POST", "/v1/admin/sounds/setup-clips"),
    ("POST", "/v1/admin/library/reindex"),
    ("POST", "/v1/admin/music/tracks-removed"),
    ("POST", "/v1/admin/library/enrich"),
    ("POST", "/v1/admin/library/enrich/repair"),
]

# CORE-2 / CORE-14: opening a live two-way mic bridge between two rooms
# from an HTTP call, with nobody in either room asked first, takes an
# admin Bearer (never the household token) AND has no pre-setup grace —
# the security tier's gate, 501 until an admin exists.
LIVE_MIC_TIER = [
    ("POST", "/v1/admin/dropin/start"),
    # A wake-word take records the room for hours: the same rule.
    ("POST", "/v1/admin/wake/record/start"),
    # A room's settings decide when and how long its microphone listens.
    ("POST", "/v1/admin/satellite/{room_id}/config"),
]

# Delta review N5 (2026-10-16): pulling new code (it moves an unclaimed
# install off the commit it was pinned to) and clearing a tamper incident
# (it lifts the container guard's freeze) are security decisions, so they
# wear the security tier: an admin Bearer, and 501 before setup.
SECURITY_TIER = [
    ("POST", "/v1/admin/version/pull"),
    ("POST", "/v1/admin/containers/incidents/{incident_id}/clear"),
]

# ADD-1 — an admin READ: the dashboard cookie may render it, nothing less.
ADMIN_READ_TIER = [
    ("GET", "/v1/admin/satellite/{room_id}/logs"),
    ("GET", "/v1/admin/satellite/{room_id}/mic_dsp"),
]

# The groups list (V060) is a household read: the cookie may render it.
def test_the_groups_list_is_a_device_read() -> None:
    gates = _gates_for("GET", "/v1/admin/music/groups")
    assert admin_auth.require_device_read in gates
    assert admin_auth.require_admin_mutation not in gates


# Device-tier reads (household token or admin Bearer).
DEVICE_READS = [
    ("GET", "/v1/voice/activity"),
    # Friend Houses, owner decision 17: what every household phone may do
    # with existing friends. The rest of Friend Houses stays on the security
    # tier (test_friend_houses_trust_stays_on_the_security_tier).
    ("GET", "/v1/admin/friend-houses"),
    ("GET", "/v1/admin/friend-houses/friends"),
    ("GET", "/v1/admin/friend-houses/conversations"),
    ("GET", "/v1/admin/friend-houses/unread"),
    ("GET", "/v1/admin/friend-houses/conversations/{house}"),
    ("POST", "/v1/admin/friend-houses/conversations/{house}/read"),
    ("POST", "/v1/admin/friend-houses/messages"),
    ("GET", "/v1/admin/friend-houses/shared-lists"),
]

# Shared lists with existing friends, open to the household since
# 2026-11-23 (the dashboard's Friend Houses page is no longer admin-only).
# Starting a share stays admin (FRIEND_HOUSES_SECURITY).
FRIEND_HOUSES_DEVICE_WRITES = [
    ("POST", "/v1/admin/friend-houses/shared-lists/{space}/accept"),
    ("POST", "/v1/admin/friend-houses/shared-lists/{space}/decline"),
    ("DELETE", "/v1/admin/friend-houses/shared-lists/{space}"),
]


@pytest.mark.parametrize(("method", "path"), FRIEND_HOUSES_DEVICE_WRITES,
                         ids=[f"{m} {p}" for m, p in FRIEND_HOUSES_DEVICE_WRITES])
def test_friend_houses_shared_list_writes_are_household(method, path) -> None:
    gates = _gates_for(method, path)
    assert admin_auth.require_device in gates, (method, path)
    assert admin_auth.require_admin_security not in gates
    assert admin_auth.require_admin_security_read not in gates

FRIEND_HOUSES_SECURITY = [
    ("PUT", "/v1/admin/friend-houses/settings"),
    ("GET", "/v1/admin/friend-houses/invites"),
    ("POST", "/v1/admin/friend-houses/invites"),
    ("GET", "/v1/admin/friend-houses/invites/{invite_id}"),
    ("DELETE", "/v1/admin/friend-houses/invites/{invite_id}"),
    ("POST", "/v1/admin/friend-houses/preview"),
    ("POST", "/v1/admin/friend-houses/requests"),
    ("GET", "/v1/admin/friend-houses/requests"),
    ("POST", "/v1/admin/friend-houses/requests/{request_id}/accept"),
    ("POST", "/v1/admin/friend-houses/requests/{request_id}/decline"),
    ("POST", "/v1/admin/friend-houses/friends/{friend_id}/verified"),
    ("PATCH", "/v1/admin/friend-houses/friends/{friend_id}"),
    ("DELETE", "/v1/admin/friend-houses/friends/{friend_id}"),
    ("PUT", "/v1/admin/friend-houses/friends/{friend_id}/alert"),
    ("GET", "/v1/admin/friend-houses/conversations/{house}/export"),
    ("DELETE", "/v1/admin/friend-houses/conversations/{house}"),
    ("POST", "/v1/admin/friend-houses/shared-lists"),
    ("POST", "/v1/admin/friend-houses/friends/{friend_id}/block"),
    ("GET", "/v1/admin/friend-houses/blocked"),
    ("DELETE", "/v1/admin/friend-houses/blocked/{house}"),
]


@pytest.mark.parametrize(("method", "path"), FRIEND_HOUSES_SECURITY,
                         ids=[f"{m} {p}" for m, p in FRIEND_HOUSES_SECURITY])
def test_friend_houses_trust_stays_on_the_security_tier(method, path) -> None:
    gates = _gates_for(method, path)
    assert (admin_auth.require_admin_security in gates
            or admin_auth.require_admin_security_read in gates), (method, path)
    assert admin_auth.require_device not in gates


def _dependency_calls(dependant: Any) -> Iterator[Any]:
    if dependant is None:
        return
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            yield dep.call
        yield from _dependency_calls(dep)


def _gates_for(method: str, path: str) -> list[Any]:
    from domovoi.tests.route_walk import iter_route_contexts

    contexts = list(iter_route_contexts(core_app.routes))
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
    ("method", "path"), GUEST_TIER, ids=[f"{m} {p}" for m, p in GUEST_TIER]
)
def test_guest_capable_actions_wear_the_guest_gate(method, path) -> None:
    """The device tier runs INSIDE the guest gate (check_queue_guest), so
    the route wears that gate and not require_device itself."""
    from domovoi import guest_queue

    gates = _gates_for(method, path)
    assert guest_queue.require_device_or_active_room_guest in gates
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
    ("method", "path"), LIVE_MIC_TIER, ids=[f"{m} {p}" for m, p in LIVE_MIC_TIER]
)
def test_an_http_drop_in_is_on_the_security_tier(method, path) -> None:
    gates = _gates_for(method, path)
    assert admin_auth.require_admin_security in gates
    assert admin_auth.require_admin_mutation not in gates
    assert admin_auth.require_device not in gates


@pytest.mark.parametrize(
    ("method", "path"), SECURITY_TIER, ids=[f"{m} {p}" for m, p in SECURITY_TIER]
)
def test_pulling_code_and_lifting_the_freeze_are_on_the_security_tier(method, path) -> None:
    gates = _gates_for(method, path)
    assert admin_auth.require_admin_security in gates
    assert admin_auth.require_admin_mutation not in gates
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


#: Core reads open on purpose, each with why.
OPEN_BY_DESIGN = {
    ("GET", "/v1/player-relay/{token}"): (
        "a room's music player (SDK 1.16 player relay) holds no credential; the token in "
        "the path is it: minted by a play, bound to one room, one remote URL and that "
        "room's player's address (domovoi/player_relay.py, test_player_relay.py)"
    ),
}


@pytest.mark.parametrize(("method", "path"), sorted(OPEN_BY_DESIGN))
def test_open_by_design_routes_wear_no_gate_and_are_marked_open(method, path) -> None:
    from domovoi.tests.route_walk import iter_route_contexts
    from domovoi.webkit import is_open_endpoint

    assert OPEN_BY_DESIGN[(method, path)].strip()
    gates = _gates_for(method, path)
    assert not any(g in gates for g in (
        admin_auth.require_device, admin_auth.require_admin_mutation,
        admin_auth.require_admin_read, admin_auth.require_device_read,
    ))
    (endpoint,) = [
        rc.dependant.call for rc in iter_route_contexts(core_app.routes)
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set())
    ]
    assert is_open_endpoint(endpoint)


@pytest.mark.asyncio
async def test_the_player_relay_answers_a_bad_token_404_not_401(monkeypatch) -> None:
    """No credential is asked for; a token that isn't good is simply not
    found."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client() as c:
        r = await c.get("/v1/player-relay/not-a-token")
    assert r.status_code == 404


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
    return (
        path.replace("{room_id}", "kitchen").replace("{action}", "pause")
        .replace("{incident_id}", "1").replace("{probe}", "versions")
    )


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
    "/v1/admin/satellite/{room_id}/voice": {"voice": "Norman"},
    "/v1/admin/satellite/{room_id}/mic_dsp": {"preset": "B"},
    "/v1/admin/satellite/{room_id}/mic_dsp/mark": {"text": "block 2, 1m"},
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

GATED_POSTS = DEVICE_TIER + ADMIN_TIER + LIVE_MIC_TIER + SECURITY_TIER
# Without a credential these answer as the guest rule says (403 for a room
# that isn't playing — test_guest_queue.py), so they sit out the 401 test
# and join the credentialed ones.
CREDENTIALED_POSTS = GATED_POSTS + GUEST_TIER
HOUSEHOLD_POSTS = DEVICE_TIER + GUEST_TIER


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
    ("method", "path"), CREDENTIALED_POSTS, ids=[f"{m} {p}" for m, p in CREDENTIALED_POSTS]
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
    ("method", "path"), HOUSEHOLD_POSTS, ids=[f"{m} {p}" for m, p in HOUSEHOLD_POSTS]
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
    ("method", "path"), ADMIN_TIER + LIVE_MIC_TIER + SECURITY_TIER,
    ids=[f"{m} {p}" for m, p in ADMIN_TIER + LIVE_MIC_TIER + SECURITY_TIER],
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
        for _method, path in DEVICE_TIER + GUEST_TIER + ADMIN_TIER:
            r = await c.post(_sample_path(path), json=BODIES.get(path, {}))
            assert r.status_code not in (401, 403, 501), f"{path}: {r.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), LIVE_MIC_TIER, ids=[f"{m} {p}" for m, p in LIVE_MIC_TIER]
)
async def test_an_http_drop_in_is_closed_before_setup(method, path, monkeypatch) -> None:
    """CORE-14: the live microphone is not part of the pre-setup grace —
    501, like the rest of the security tier, until an admin exists (and
    again after --reset-admin), whatever is presented."""
    install_fake_db(monkeypatch, admin=False, device_token=DEVICE_TOKEN)
    async with _client() as c:
        for headers in ({}, {HEADER: DEVICE_TOKEN}):
            r = await c.post(
                _sample_path(path), json=BODIES.get(path, {}), headers=headers
            )
            assert r.status_code == 501, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), SECURITY_TIER, ids=[f"{m} {p}" for m, p in SECURITY_TIER]
)
async def test_pulling_code_and_lifting_the_freeze_are_closed_before_setup(
    method, path, monkeypatch
) -> None:
    """Delta review N5: no pre-setup grace for either — 501 whatever is
    presented until an admin password exists."""
    install_fake_db(monkeypatch, admin=False, device_token=DEVICE_TOKEN)
    async with _client() as c:
        for headers in ({}, {HEADER: DEVICE_TOKEN}, bearer(ADMIN_TOKEN)):
            r = await c.post(
                _sample_path(path), json=BODIES.get(path, {}), headers=headers
            )
            assert r.status_code == 501, r.text


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


@pytest.mark.parametrize(("method", "path"), DEVICE_READS, ids=[f"{m} {p}" for m, p in DEVICE_READS])
def test_device_reads_are_on_the_device_tier(method, path) -> None:
    gates = _gates_for(method, path)
    assert admin_auth.require_device in gates
    assert admin_auth.require_admin_mutation not in gates


FRIEND_HOUSES_HOUSEHOLD = [mp for mp in DEVICE_READS if "/friend-houses" in mp[1]] + FRIEND_HOUSES_DEVICE_WRITES


@pytest.mark.parametrize(("method", "path"), FRIEND_HOUSES_HOUSEHOLD,
                         ids=[f"{m} {p}" for m, p in FRIEND_HOUSES_HOUSEHOLD])
def test_friend_houses_household_routes_have_no_pre_setup_grace_gate(method, path) -> None:
    assert admin_auth.require_device_after_setup in _gates_for(method, path)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), FRIEND_HOUSES_HOUSEHOLD,
                         ids=[f"{m} {p}" for m, p in FRIEND_HOUSES_HOUSEHOLD])
async def test_friend_houses_household_routes_are_closed_before_setup(
    method, path, monkeypatch
) -> None:
    """After --reset-admin the friends and messages are still there, so the
    household routes answer 501, whatever is presented, until an admin
    exists again."""
    install_fake_db(monkeypatch, admin=False, device_token=DEVICE_TOKEN)
    url = path.replace("{house}", "a" * 56).replace("{space}", "b" * 32)
    async with _client() as c:
        for headers in ({}, {HEADER: DEVICE_TOKEN}):
            r = await c.request(method, url, json={} if method in ("POST", "PUT") else None,
                                headers=headers)
            assert r.status_code == 501, (path, r.status_code, r.text)


@pytest.mark.asyncio
async def test_voice_activity_says_busy_and_nothing_else_to_the_household_token(monkeypatch) -> None:
    """Documents plan D2, review round 1: refused with no credential after
    setup; with the household token, {busy} only (no counts or timings)."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client() as c:
        assert (await c.get("/v1/voice/activity")).status_code == 401
        r = await c.get("/v1/voice/activity", headers={HEADER: DEVICE_TOKEN})
    assert r.status_code == 200 and r.json() == {"busy": False}


def _friend_apps_routes() -> list[tuple[str, str]]:
    """Every core route for apps with friends (SDK 1.18): the per-friend
    toggle, allow/revoke, resume and the list. None until the plugin mail
    service (slice 4) adds them; then each must be here."""
    from domovoi.tests.route_walk import iter_route_contexts

    return sorted({(m, rc.path) for rc in iter_route_contexts(core_app.routes)
                   if getattr(rc, "path", "").startswith("/v1/admin/friend-houses")
                   and "/apps" in rc.path for m in (getattr(rc, "methods", None) or ())})


def test_apps_with_friends_stays_on_the_security_tier() -> None:
    # The design note §6.11: security tier at both hops; inviting and
    # playing are on the plugin's own device-tier routes, never these.
    household = DEVICE_READS + FRIEND_HOUSES_DEVICE_WRITES
    assert not [r for r in household if "/apps" in r[1]]
    for method, path in _friend_apps_routes():
        gates = _gates_for(method, path)
        assert (admin_auth.require_admin_security in gates
                or admin_auth.require_admin_security_read in gates), (method, path)
        assert admin_auth.require_device not in gates and admin_auth.require_device_read not in gates
