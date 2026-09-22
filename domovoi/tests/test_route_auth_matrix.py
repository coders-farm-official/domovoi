"""Route-walk over BOTH FastAPI apps: every mutating route (POST / PUT /
PATCH / DELETE) must wear an auth dependency, or be named in
:data:`ALLOWLIST` with a reason.

DB-free by construction — this only inspects the route tables, so it can
never hide behind ``requires_db``. The walk is parametrized, one test per
route, so a failure names the exact route that lost (or never had) its
gate.

The allowlist is the honest picture of what is intentionally open TODAY.
It is meant to SHRINK: when a later change gates one of these routes, the
companion test ``test_allowlist_entries_are_still_open`` fails until the
entry is removed, so the list can never quietly go stale. Adding a new
mutating route without a gate fails the walk until the route is either
gated or allowlisted with a written reason.

Gates that count (a route may wear any of them, at the route or the
router level, directly or through another dependency):

* :func:`domovoi.admin_auth.require_admin_mutation` — admin Bearer,
  pre-setup grace;
* :func:`domovoi.admin_auth.require_admin_security` — admin Bearer,
  fails closed before setup;
* :func:`domovoi.auth.require_admin` — plugin management, fails closed;
* :func:`domovoi.admin_auth.require_device` — the household device
  token OR an admin Bearer;
* :func:`domovoi.admin_auth.require_chat_callback` — the per-boot secret
  the chat agent's generated proxy tools carry (the one machine-to-machine
  caller that can hold neither tier's credential).

``require_admin_read`` deliberately does NOT count for a mutating route:
the dashboard cookie renders GET state and never authorizes a change.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from domovoi import admin_auth
from domovoi.auth import require_admin
from domovoi.main import app as core_app
from web.backend.main import app as web_app

MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")

AUTH_GATES: tuple[Any, ...] = (
    admin_auth.require_admin_mutation,
    admin_auth.require_admin_security,
    admin_auth.require_device,
    admin_auth.require_chat_callback,
    require_admin,
)

_KIOSK = "kiosk transport row — FE-3 keeps it open by design (display.jsx)"

# (app, method, path) -> why it is open. Every entry is a route that has
# NO gate today; the reason says which tier owns it and, where known,
# which security batch is expected to close it.
ALLOWLIST: dict[tuple[str, str, str], str] = {
    # ── core: open by design ──────────────────────────────────────────
    # Everything else the core mutates through now wears a tier (B2):
    # the ordinary household actions take require_device, the
    # code-adjacent and physical-effect ones take require_admin_mutation,
    # and the chat agent's callback takes its per-boot secret.
    ("core", "POST", "/v1/admin/music/add-by-url"): (
        "outbound-fetch tier — check_outbound_fetch inside the handler (admin "
        "OR provider allowlist + per-source rate limit)"
    ),
    # ── web: the auth surface itself ──────────────────────────────────
    ("web", "POST", "/api/auth/setup"): "IS the auth surface — requires the setup code, backoff-throttled",
    ("web", "POST", "/api/auth/login"): "IS the auth surface — password + backoff",
    ("web", "POST", "/api/auth/logout"): "checks the Bearer inside the handler (401 without)",
    ("web", "DELETE", "/api/auth/sessions/{token_hash}"): "checks the Bearer inside the handler",
    ("web", "POST", "/api/auth/password"): "checks the Bearer inside the handler",
    # ── web: proxies whose gate is applied by the core (auth forwarded) ─
    ("web", "POST", "/api/plugins/install"): "proxy — core require_admin (fail-closed) gates it",
    ("web", "POST", "/api/plugins/install/{staged_id}/confirm"): "proxy — core require_admin gates it",
    ("web", "POST", "/api/plugins/{slug}/enable"): "proxy — core require_admin gates it",
    ("web", "POST", "/api/plugins/{slug}/disable"): "proxy — core require_admin gates it",
    ("web", "POST", "/api/plugins/{slug}/uninstall"): "proxy — core require_admin gates it",
    ("web", "POST", "/api/plugins/{slug}/upgrade"): "proxy — core require_admin gates it",
    ("web", "POST", "/api/music/add-by-url"): "proxy — core check_outbound_fetch gates it",
    # ── web: the video satellite's kiosk transport row (FE-3) ─────────
    # Kamron's decision: the kiosk renders unattended, with nobody in the
    # room to hold a credential, so its four transport verbs stay open.
    # Pinned from the other side by
    # ``domovoi/tests/test_kiosk_surface_is_documented.py``.
    ("web", "POST", "/api/music/pause/{room_id}"): _KIOSK,
    ("web", "POST", "/api/music/resume/{room_id}"): _KIOSK,
    ("web", "POST", "/api/music/stop/{room_id}"): _KIOSK,
    ("web", "POST", "/api/music/skip/{room_id}"): _KIOSK,
    # ── web: daily tier — device tier in wave 2 (B2 / B4) ─────────────
    ("web", "PATCH", "/api/satellites/{room_id}"): "daily tier — cosmetic label",
    ("web", "DELETE", "/api/satellites/{room_id}/timers/{timer_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/satellites/{room_id}/announce"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/satellites/announce-all"): "daily tier — device tier in wave 2",
    # CORE-2: the core routes behind these are gated now (start takes an
    # admin Bearer, end the household token) and this hop forwards the
    # caller's credentials, so an ungated proxy cannot open a call.
    ("web", "POST", "/api/satellites/{room_id}/dropin/start"): "proxy — core require_admin_mutation gates it (auth forwarded)",
    ("web", "POST", "/api/satellites/{room_id}/dropin/end"): "proxy — core require_device gates it (auth forwarded)",
    ("web", "POST", "/api/satellites/{room_id}/volume"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/satellites/{room_id}/restart"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/satellites/{room_id}/display"): "daily tier — device tier in wave 2",
    ("web", "PATCH", "/api/satellites/{room_id}/config"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/calendar/events"): "daily tier — device tier in wave 2",
    ("web", "PATCH", "/api/calendar/events/{event_id}"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/calendar/events/{event_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/config/version/check"): "read-shaped git fetch — device tier in wave 2",
    ("web", "POST", "/api/config/version/pull"): "git pull --ff-only — device tier in wave 2",
    ("web", "POST", "/api/playlists"): "daily tier — device tier in wave 2",
    ("web", "PATCH", "/api/playlists/{playlist_id}"): "daily tier — device tier in wave 2",
    ("web", "PATCH", "/api/playlists/{playlist_id}/order"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/playlists/{playlist_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/playlists/{playlist_id}/tracks"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/playlists/{playlist_id}/tracks/{track_id}"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/podcasts/subscriptions/{sub_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/podcasts/positions/{episode_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/audiobooks/reindex"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/audiobooks/{book_id}/position"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/chat/threads"): "daily tier — device tier in wave 2",
    ("web", "PATCH", "/api/chat/threads/{thread_id}"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/chat/threads/{thread_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/chat/threads/{thread_id}/messages"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/chat/uploads"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/models/active"): (
        "proxy — the core's security tier gates the config write it forwards to"
    ),
    ("web", "POST", "/api/news/people/{person_id}/topics"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/news/topics/{topic_id}"): "daily tier — device tier in wave 2",
    ("web", "DELETE", "/api/news/topics/{topic_id}/feeds/{feed_id}"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/news/items/{item_id}/favorite"): "daily tier — device tier in wave 2",
    ("web", "POST", "/api/news/poll"): "daily tier — device tier in wave 2",
}


# ─── Route walking ─────────────────────────────────────────────────────────


def _dependency_calls(dependant: Any) -> Iterator[Any]:
    """Every callable in a route's dependency tree (route-level
    ``dependencies=[...]``, router-level ones folded in by include_router,
    and parameters declared with ``Depends``), recursively."""
    if dependant is None:
        return
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            yield dep.call
        yield from _dependency_calls(dep)


def _route_records(app: FastAPI, label: str) -> list[tuple[str, str, str, Any]]:
    """``(app, METHOD, path, dependant)`` for every mutating route, with
    included routers flattened (FastAPI >= 0.139 keeps them nested as
    ``_IncludedRouter`` entries; ``iter_route_contexts`` resolves the
    effective path + dependencies; older releases flatten on include)."""
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # pragma: no cover — older FastAPI flattens itself
        contexts = [r for r in app.routes if isinstance(r, APIRoute)]
    else:
        contexts = list(iter_route_contexts(app.routes))
    out: list[tuple[str, str, str, Any]] = []
    for rc in contexts:
        methods = getattr(rc, "methods", None) or set()
        path = getattr(rc, "path", None)
        dependant = getattr(rc, "dependant", None)
        if not path or dependant is None:
            continue
        for method in MUTATING_METHODS:
            if method in methods:
                out.append((label, method, path, dependant))
    return out


ROUTES = _route_records(core_app, "core") + _route_records(web_app, "web")
ROUTE_IDS = [f"{a} {m} {p}" for a, m, p, _ in ROUTES]


def _is_gated(dependant: Any) -> bool:
    return any(call in AUTH_GATES for call in _dependency_calls(dependant))


def test_the_walk_saw_both_apps() -> None:
    """Guard against an import-time regression silently emptying the
    walk (which would make every other test here pass vacuously)."""
    apps = {a for a, _, _, _ in ROUTES}
    assert apps == {"core", "web"}
    assert len(ROUTES) > 100


@pytest.mark.parametrize(("label", "method", "path", "dependant"), ROUTES, ids=ROUTE_IDS)
def test_mutating_route_is_gated_or_allowlisted(label, method, path, dependant) -> None:
    if _is_gated(dependant):
        return
    key = (label, method, path)
    assert key in ALLOWLIST, (
        f"{label} {method} {path} has no auth dependency "
        f"(require_admin_mutation / require_admin_security / require_admin / "
        f"require_device) and is not in ALLOWLIST — gate it, or allowlist it "
        f"with a reason"
    )
    assert ALLOWLIST[key].strip(), f"{key} is allowlisted without a reason"


def test_allowlist_entries_are_still_open() -> None:
    """Every allowlist entry must name a route that EXISTS and is still
    ungated — once a route grows a gate, its entry has to go, so the
    list only ever shrinks toward the truth."""
    by_key = {(a, m, p): d for a, m, p, d in ROUTES}
    stale = [k for k in ALLOWLIST if k not in by_key]
    assert not stale, f"ALLOWLIST names routes that no longer exist: {stale}"
    gated = [k for k in ALLOWLIST if _is_gated(by_key[k])]
    assert not gated, f"ALLOWLIST entries are now gated — remove them: {gated}"


# ─── The security tier (CORE-5): fail-closed gate on the specific routes ──

SECURITY_TIER_ROUTES = [
    ("core", "POST", "/v1/admin/config"),
    ("core", "POST", "/v1/admin/version/restart"),
    ("core", "POST", "/v1/admin/satellite/upgrade"),
    ("core", "POST", "/v1/admin/satellites/{room_id}/pairing/preseed"),
    ("core", "DELETE", "/v1/admin/satellites/{room_id}/pairing"),
    ("core", "DELETE", "/v1/admin/satellites/{room_id}"),
    ("core", "POST", "/v1/admin/device-token/rotate"),
    ("web", "PATCH", "/api/config/editable"),
    ("web", "POST", "/api/config/version/restart"),
    ("web", "POST", "/api/satellites/{room_id}/upgrade"),
    ("web", "POST", "/api/satellites/{room_id}/pairing/reset"),
    ("web", "POST", "/api/satellites/pending/{pending_id}/adopt"),
    ("web", "DELETE", "/api/satellites/{room_id}"),
    ("web", "POST", "/api/auth/device-token/rotate"),
]


@pytest.mark.parametrize(
    ("label", "method", "path"), SECURITY_TIER_ROUTES,
    ids=[f"{a} {m} {p}" for a, m, p in SECURITY_TIER_ROUTES],
)
def test_security_tier_route_fails_closed_before_setup(label, method, path) -> None:
    """The security tier wears ``require_admin_security`` specifically —
    the gate that answers 501 before setup — not the grace-keeping
    ``require_admin_mutation``."""
    by_key = {(a, m, p): d for a, m, p, d in ROUTES}
    assert (label, method, path) in by_key, f"{label} {method} {path} is missing"
    calls = list(_dependency_calls(by_key[(label, method, path)]))
    assert admin_auth.require_admin_security in calls, (
        f"{label} {method} {path} must depend on require_admin_security"
    )
    assert admin_auth.require_admin_mutation not in calls


def test_device_token_reads_are_admin_reads_that_fail_closed() -> None:
    """``GET /v1/admin/device-token`` and ``GET /api/auth/device-token``
    render to a Bearer or the cookie and 501 before setup."""
    found = {}
    for app, label in ((core_app, "core"), (web_app, "web")):
        try:
            from fastapi.routing import iter_route_contexts

            contexts = list(iter_route_contexts(app.routes))
        except ImportError:  # pragma: no cover
            contexts = [r for r in app.routes if isinstance(r, APIRoute)]
        for rc in contexts:
            path = getattr(rc, "path", None)
            if path in ("/v1/admin/device-token", "/api/auth/device-token") and "GET" in (
                getattr(rc, "methods", None) or set()
            ):
                found[(label, path)] = list(_dependency_calls(getattr(rc, "dependant", None)))
    assert set(found) == {("core", "/v1/admin/device-token"), ("web", "/api/auth/device-token")}
    for key, calls in found.items():
        assert admin_auth.require_admin_security_read in calls, key
