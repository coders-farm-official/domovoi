"""F-013: the Greetings, Voices and Wake Words routers carry the admin
mutation gate on every non-GET route.

These three routers decide what every satellite says, in whose voice, and
what the house listens for — and a Piper upload puts a model file on the
server. They shipped with no ``require_admin_mutation`` at all: anyone on
the LAN could add, rename, retrain or delete any of it. The existing
router tests (``test_wake_words_api`` etc.) all run under the pre-setup
grace on a fresh test DB, where the gate is invisible whether or not it is
there — so the gate is proven here from the other side, DB-free:

* a structural walk of each router asserting the dependency is attached
  to every POST / PATCH / DELETE and to no GET;
* HTTP requests through the real ``require_admin_mutation`` with only the
  request classifier stubbed (no Postgres, no lifespan): no credential →
  401, dashboard cookie only → 403, live Bearer → the endpoint is reached.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import web.backend.api.greetings as greetings_api
import web.backend.api.voices as voices_api
import web.backend.api.wake_words as wake_words_api
from domovoi import admin_auth
from domovoi.admin_auth import require_admin_mutation
from web.backend.main import app

ROUTERS = {
    "greetings": greetings_api.router,
    "voices": voices_api.router,
    "wake_words": wake_words_api.router,
}

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _has_gate(route: APIRoute) -> bool:
    """True when ``require_admin_mutation`` is anywhere in the route's
    resolved dependency tree (``dependencies=[Depends(...)]`` lands there)."""
    stack = list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is require_admin_mutation:
            return True
        stack.extend(d.dependencies)
    return False


def _routes(router) -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute)]


def _mutations(router) -> list[APIRoute]:
    return [r for r in _routes(router) if not (r.methods <= _READ_METHODS)]


# ─── Structural: every mutation is gated, no read is ───────────────────────


@pytest.mark.parametrize("name", sorted(ROUTERS))
def test_every_mutation_route_carries_the_admin_gate(name: str) -> None:
    router = ROUTERS[name]
    mutations = _mutations(router)
    assert mutations, f"{name}: no mutation routes found — did the router move?"
    ungated = [
        f"{sorted(r.methods)} {r.path}" for r in mutations if not _has_gate(r)
    ]
    assert ungated == [], f"{name}: mutations without require_admin_mutation: {ungated}"


@pytest.mark.parametrize("name", sorted(ROUTERS))
def test_read_routes_stay_open(name: str) -> None:
    router = ROUTERS[name]
    reads = [r for r in _routes(router) if r.methods <= _READ_METHODS]
    assert reads, f"{name}: expected at least one GET"
    gated = [f"{sorted(r.methods)} {r.path}" for r in reads if _has_gate(r)]
    assert gated == [], f"{name}: reads must not need admin: {gated}"


def test_the_expected_surface_is_what_got_gated() -> None:
    """Pin the shape so a route added later without the gate is a loud
    failure in the parametrized test above, not a silent shrink here."""
    counts = {name: len(_mutations(r)) for name, r in ROUTERS.items()}
    assert counts == {"greetings": 3, "voices": 4, "wake_words": 12}


# ─── HTTP: the gate through the real dependency, classifier stubbed ────────


def _classify(monkeypatch, result: str) -> None:
    async def _fake(request, session=None):
        return result

    monkeypatch.setattr(admin_auth, "check_admin_request", _fake)


class _EndpointReached(Exception):
    """Raised from a stubbed session_scope so a request that passes the gate
    is recognisable without a database."""


@pytest.fixture
def endpoint_trips(monkeypatch):
    """Every DB touch in the three routers raises _EndpointReached, and the
    core proxies are stubbed likewise, so 'the handler ran' is observable
    and nothing needs Postgres or a live core."""

    class _Scope:
        async def __aenter__(self):
            raise _EndpointReached()

        async def __aexit__(self, *a):
            return False

    for mod in (greetings_api, voices_api, wake_words_api):
        monkeypatch.setattr(mod, "session_scope", lambda: _Scope())

    async def _post_admin(*a, **kw):
        raise _EndpointReached()

    monkeypatch.setattr(wake_words_api, "post_admin", _post_admin)
    monkeypatch.setattr(greetings_api, "post_admin", _post_admin)
    monkeypatch.setattr(voices_api, "post_admin", _post_admin)


_PATH_SAMPLES = {
    "{greeting_id}": "1",
    "{voice_id}": "1",
    "{wake_word_id}": "1",
    "{name}": "clip-0001.wav",
}


def _sample_url(route: APIRoute) -> str:
    url = route.path
    for k, v in _PATH_SAMPLES.items():
        url = url.replace(k, v)
    assert "{" not in url, f"unhandled path param in {route.path}"
    return url


def _all_mutation_requests() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for router in ROUTERS.values():
        for r in _mutations(router):
            for m in sorted(r.methods - _READ_METHODS):
                out.append((m, _sample_url(r)))
    return out


_MUTATION_REQUESTS = _all_mutation_requests()


def _bare_client(**kw) -> TestClient:
    """No ``with`` — the lifespan (poll loop, LISTEN task) never starts, so
    no background task opens a database connection."""
    return TestClient(app, **kw)


@pytest.mark.parametrize("method,url", _MUTATION_REQUESTS, ids=lambda x: str(x))
def test_no_credential_is_401_on_every_mutation(
    method: str, url: str, monkeypatch, endpoint_trips
) -> None:
    _classify(monkeypatch, "no-auth")
    r = _bare_client().request(method, url, json={})
    assert r.status_code == 401, f"{method} {url}: {r.status_code} {r.text}"
    assert "admin" in r.json()["detail"]


@pytest.mark.parametrize("method,url", _MUTATION_REQUESTS, ids=lambda x: str(x))
def test_dashboard_cookie_alone_is_403_on_every_mutation(
    method: str, url: str, monkeypatch, endpoint_trips
) -> None:
    """A valid dashboard cookie renders GET state but never authorises a
    mutation (CSRF stance) — the gate must say 403, not let it through."""
    _classify(monkeypatch, "cookie-only")
    r = _bare_client().request(method, url, json={})
    assert r.status_code == 403, f"{method} {url}: {r.status_code} {r.text}"
    assert "Bearer" in r.json()["detail"]


@pytest.mark.parametrize(
    "method,url,body",
    [
        ("POST", "/api/greetings", {"text": "hello there", "category": "generic"}),
        ("DELETE", "/api/greetings/1", None),
        ("POST", "/api/voices/edge", {"name": "aria", "voice_id": "en-US-AriaNeural"}),
        ("DELETE", "/api/voices/1", None),
        ("POST", "/api/wake-words", {"name": "Hey Domovoi", "phrase": "hey domovoi"}),
        ("DELETE", "/api/wake-words/1", None),
        ("DELETE", "/api/wake-words/1/clips/clip-0001.wav", None),
    ],
)
def test_a_live_bearer_session_reaches_the_endpoint(
    method: str, url: str, body, monkeypatch, endpoint_trips
) -> None:
    """Positive control: with the classifier saying 'ok' the gate opens and
    the handler runs (observed as the stubbed DB/proxy being reached)."""
    _classify(monkeypatch, "ok")
    with pytest.raises(_EndpointReached):
        _bare_client(raise_server_exceptions=True).request(
            method, url, json=body, headers={"Authorization": "Bearer t"}
        )


def test_pre_setup_grace_still_lets_a_fresh_install_work(monkeypatch, endpoint_trips):
    """Before an admin credential exists the daily surfaces keep working —
    the same grace every other require_admin_mutation route has, and what
    the existing router tests rely on."""
    _classify(monkeypatch, "pre-setup")
    with pytest.raises(_EndpointReached):
        _bare_client(raise_server_exceptions=True).post(
            "/api/greetings", json={"text": "hi", "category": "generic"}
        )
