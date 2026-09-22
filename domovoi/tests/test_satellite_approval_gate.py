"""What guards a satellite approval (CORE-3).

Approval is the moment a room name becomes a bound device, so four things
have to hold and none of them need a database to prove:

* the pending list is an admin READ at both hops — it names the rooms a
  household is about to bind, and how long each has waited;
* approving takes the code the device is showing, and the route refuses to
  go near the pairing tables without one;
* attempts against a room are budgeted, so the code cannot be walked;
* parking a room is budgeted per source, and a device that is not the one
  already parked under that name is told so instead of replacing it.

The auth primitives are faked (``install_fake_db``) and the approval
repository is stubbed, because every answer here is decided before the
handler reaches Postgres. The DB-backed matrices live in
test_satellite_approvals.py and test_satellite_pairing.py.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi import main as core_main
from domovoi import streaming as streaming_mod
from domovoi.main import app as core_app
from domovoi.streaming import StreamSession
from domovoi.tests.auth_testkit import bearer, install_fake_db
from web.backend.main import app as web_app

ADMIN_TOKEN = "admin-token"
CODE = "481502"
APPROVALS_CORE = "/v1/admin/satellites/approvals"
APPROVALS_WEB = "/api/satellites/approvals"


def _client(app) -> AsyncClient:
    # raise_app_exceptions=False: a request that PASSES the gate goes on to
    # a handler that wants Postgres and fails — which is the proof we want
    # ("the gate let it through"), not an error to chase.
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


# ─── Structure: which gate each route wears ──────────────────────────────


def _dependency_calls(dependant: Any) -> Iterator[Any]:
    if dependant is None:
        return
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            yield dep.call
        yield from _dependency_calls(dep)


def _gates_for(app, method: str, path: str) -> list[Any]:
    try:
        from fastapi.routing import iter_route_contexts

        contexts = list(iter_route_contexts(app.routes))
    except ImportError:  # pragma: no cover — older FastAPI flattens itself
        from fastapi.routing import APIRoute

        contexts = [r for r in app.routes if isinstance(r, APIRoute)]
    for rc in contexts:
        if getattr(rc, "path", None) == path and method in (
            getattr(rc, "methods", None) or set()
        ):
            return list(_dependency_calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route on that app")


def test_the_core_pending_list_is_an_admin_read() -> None:
    assert admin_auth.require_admin_read in _gates_for(core_app, "GET", APPROVALS_CORE)


def test_the_web_pending_list_is_an_admin_read() -> None:
    assert admin_auth.require_admin_read in _gates_for(web_app, "GET", APPROVALS_WEB)


def test_approving_stays_on_the_admin_tier() -> None:
    """Gating the list must not have quietly relaxed the decision itself."""
    gates = _gates_for(core_app, "POST", f"{APPROVALS_CORE}/{{room_id}}/approve")
    assert admin_auth.require_admin_mutation in gates


# ─── Behaviour: what an uncredentialed caller gets ───────────────────────


@pytest.mark.asyncio
async def test_the_pending_list_refuses_a_caller_with_no_session(monkeypatch) -> None:
    state = install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    for app, path in ((core_app, APPROVALS_CORE), (web_app, APPROVALS_WEB)):
        async with _client(app) as c:
            assert (await c.get(path)).status_code == 401
            # The same request WITH a session is no longer refused by the
            # gate (whatever the handler then makes of it).
            assert (
                await c.get(path, headers=bearer(ADMIN_TOKEN))
            ).status_code != 401
    # Before setup the daily surface is still open — the gate's own grace.
    state.admin = False
    async with _client(core_app) as c:
        assert (await c.get(APPROVALS_CORE)).status_code != 401


# ─── Behaviour: the code is required, compared, and budgeted ─────────────


class _FakeApprovals:
    """Stands in for the repository: records the codes it was asked about
    and answers with whatever the test lined up."""

    answers: list[str] = []
    seen: list[tuple[str, str]] = []

    def __init__(self, session: Any) -> None:
        pass

    async def approve(self, room_id: str, code: str) -> str:
        type(self).seen.append((room_id, code))
        return type(self).answers.pop(0) if type(self).answers else "mismatch"


@pytest.fixture
def fake_approvals(monkeypatch):
    _FakeApprovals.answers = []
    _FakeApprovals.seen = []

    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(core_main, "session_scope", fake_scope)
    monkeypatch.setattr(core_main, "SatelliteApprovalRepository", _FakeApprovals)
    core_main.APPROVAL_CODE_LIMITER.reset()
    yield _FakeApprovals
    core_main.APPROVAL_CODE_LIMITER.reset()


def _approve_url(room: str = "kitchen") -> str:
    return f"{APPROVALS_CORE}/{room}/approve"


@pytest.mark.asyncio
async def test_approving_without_a_code_never_reaches_the_tables(
    monkeypatch, fake_approvals
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(core_app) as c:
        for body in ({}, {"code": ""}, {"code": "   "}, {"code": "abc123"}):
            r = await c.post(_approve_url(), json=body, headers=bearer(ADMIN_TOKEN))
            assert r.status_code == 400, (body, r.text)
    assert fake_approvals.seen == []


@pytest.mark.asyncio
async def test_a_wrong_code_is_refused_and_the_right_one_approves(
    monkeypatch, fake_approvals
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    fake_approvals.answers = ["mismatch", "approved"]
    async with _client(core_app) as c:
        wrong = await c.post(
            _approve_url(), json={"code": "000000"}, headers=bearer(ADMIN_TOKEN)
        )
        right = await c.post(
            _approve_url(), json={"code": CODE}, headers=bearer(ADMIN_TOKEN)
        )
    assert wrong.status_code == 403
    assert right.status_code == 200 and right.json()["approved"] is True
    # The handler hands the code through untouched apart from trimming.
    assert fake_approvals.seen == [("kitchen", "000000"), ("kitchen", CODE)]


@pytest.mark.asyncio
async def test_nothing_pending_and_no_code_on_file_are_conflicts(
    monkeypatch, fake_approvals
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    fake_approvals.answers = ["not_pending", "no_code"]
    async with _client(core_app) as c:
        for _ in range(2):
            r = await c.post(
                _approve_url(), json={"code": CODE}, headers=bearer(ADMIN_TOKEN)
            )
            assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_attempts_against_one_room_are_budgeted(
    monkeypatch, fake_approvals
) -> None:
    """Six digits only buys anything if the guesses are counted."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    limit = core_main.APPROVAL_CODE_MAX_ATTEMPTS
    fake_approvals.answers = ["mismatch"] * limit
    async with _client(core_app) as c:
        for _ in range(limit):
            r = await c.post(
                _approve_url(), json={"code": "000000"}, headers=bearer(ADMIN_TOKEN)
            )
            assert r.status_code == 403
        blocked = await c.post(
            _approve_url(), json={"code": CODE}, headers=bearer(ADMIN_TOKEN)
        )
        # Another room is unaffected — one satellite's bad run must not
        # lock the household out of approving a different one.
        other = await c.post(
            _approve_url("garage"), json={"code": CODE},
            headers=bearer(ADMIN_TOKEN),
        )
    assert blocked.status_code == 429
    assert other.status_code != 429
    # The throttled attempt never got as far as a comparison.
    assert len(fake_approvals.seen) == limit + 1


# ─── Parking: per-source budget, conflict, and the code that rides back ──


class _FakeWS:
    """WebSocket stand-in: records frames, and carries a peer address so
    the per-source budget has something to key on."""

    def __init__(self, host: str = "192.168.1.50") -> None:
        self.sent: list[str] = []
        self.client = type("C", (), {"host": host})()
        self.headers: dict[str, str] = {}

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:  # pragma: no cover
        pass


def _session(room_id: str = "kitchen", host: str = "192.168.1.50") -> StreamSession:
    return StreamSession(_FakeWS(host), room_id)  # type: ignore[arg-type]


def _last_frame(sess: StreamSession) -> dict[str, Any]:
    return json.loads(sess.ws.sent[-1])


class _FakeApprovalRepo:
    outcome = "parked"
    parked_code: str | None = CODE
    calls: list[dict[str, Any]] = []

    def __init__(self, session: Any) -> None:
        pass

    async def request(self, room_id: str, **kw: Any) -> tuple[str, str | None]:
        type(self).calls.append({"room_id": room_id, **kw})
        return type(self).outcome, type(self).parked_code


@pytest.fixture
def fake_park(monkeypatch):
    _FakeApprovalRepo.outcome = "parked"
    _FakeApprovalRepo.parked_code = CODE
    _FakeApprovalRepo.calls = []
    monkeypatch.setattr(streaming_mod, "SatelliteApprovalRepository", _FakeApprovalRepo)
    streaming_mod.APPROVAL_HELLO_LIMITER.reset()
    yield _FakeApprovalRepo
    streaming_mod.APPROVAL_HELLO_LIMITER.reset()


@pytest.mark.asyncio
async def test_parking_tells_the_device_the_code_on_file(fake_park) -> None:
    """The device says the code out loud, so it has to be told the one the
    operator will be asked for — which on a retry is the code already on
    file, not the one just offered."""
    sess = _session()
    fake_park.parked_code = CODE
    accepted = await sess._park_for_approval(
        object(), {"mac": "b8:27:eb:aa:bb:cc"}, token_hash="a" * 64, code="999999"
    )
    assert accepted is False
    frame = _last_frame(sess)
    assert frame["reason"] == "awaiting_approval"
    assert frame["code"] == CODE


@pytest.mark.asyncio
async def test_a_device_with_no_code_is_given_one(fake_park) -> None:
    """A satellite that never ran the portal still parks with something
    the operator can be asked for, rather than an unapprovable row."""
    fake_park.parked_code = None
    sess = _session()
    await sess._park_for_approval(object(), {}, token_hash="a" * 64, code=None)
    minted = fake_park.calls[-1]["code"]
    assert minted is not None and len(minted) == 6 and minted.isdigit()
    assert _last_frame(sess)["code"] == minted


@pytest.mark.asyncio
async def test_a_different_device_under_a_parked_room_is_a_conflict(
    fake_park,
) -> None:
    fake_park.outcome = "conflict"
    fake_park.parked_code = None
    sess = _session()
    accepted = await sess._park_for_approval(
        object(), {}, token_hash="b" * 64, code=CODE
    )
    assert accepted is False
    frame = _last_frame(sess)
    assert frame["reason"] == "approval_conflict"
    # Nothing about the parked request is echoed back to the caller.
    assert "code" not in frame


@pytest.mark.asyncio
async def test_approval_requests_are_budgeted_per_source(fake_park) -> None:
    """One address cannot fill the approvals list with invented rooms and
    bury the device someone is standing next to."""
    budget = streaming_mod.APPROVAL_HELLO_MAX_PER_WINDOW
    for i in range(budget):
        sess = _session(f"room{i}")
        await sess._park_for_approval(object(), {}, token_hash="a" * 64, code=CODE)
        assert _last_frame(sess)["reason"] == "awaiting_approval"

    blocked = _session("roomN")
    # A session object would raise if it were used — the throttle answers
    # before anything reaches the database.
    accepted = await blocked._park_for_approval(
        None, {}, token_hash="a" * 64, code=CODE
    )
    assert accepted is False
    assert _last_frame(blocked)["reason"] == "approval_throttled"
    assert len(fake_park.calls) == budget

    # A different address still gets through: the budget is per source.
    other = _session("roomN", host="192.168.1.77")
    await other._park_for_approval(object(), {}, token_hash="a" * 64, code=CODE)
    assert _last_frame(other)["reason"] == "awaiting_approval"


def test_the_source_key_is_the_peer_unless_the_peer_is_trusted() -> None:
    """A forwarded address counts only from a trusted peer — otherwise it
    is a header anyone can rotate for a fresh budget."""
    sess = _session(host="192.168.1.50")
    sess.ws.headers = {"x-forwarded-for": "10.0.0.9"}
    assert sess._hello_source() == "192.168.1.50"

    trusted = _session(host="127.0.0.1")
    trusted.ws.headers = {"x-forwarded-for": "10.0.0.9, 10.0.0.1"}
    assert trusted._hello_source() == "10.0.0.9"


def test_a_socket_with_no_peer_does_not_break_the_path() -> None:
    class _Bare:
        async def send_text(self, data: str) -> None:
            pass

    sess = StreamSession(_Bare(), "kitchen")  # type: ignore[arg-type]
    assert sess._hello_source() == "unknown"
