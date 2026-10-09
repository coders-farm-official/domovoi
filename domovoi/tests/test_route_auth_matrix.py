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

Plugin routes are not in either static table — they mount at runtime
behind ``webkit.enforce_route_tier`` — so the last section walks the
bundled radio plugin's real routers and holds each mutation to its tier
(``@device_endpoint`` / ``@open_endpoint`` / the admin default).

One class of READ is walked too: household speech and personal content
(conversation turns, voice notes, memories, favorites, preferences, chat,
wake-word recordings) is for paired devices only (owner decision
2026-09-26), so every such GET must wear ``require_device_read`` — and the
reads beside them that were deliberately left open are pinned open, so
moving one is a decision rather than a drive-by. The speech latency
summary, open by design until 2026-10-08, is a household read now (a
turn count for one room says somebody just spoke there). The opt-in
command recordings (2026-09-28) sit higher than
the rest of the household's speech: ``require_admin_security_read``, never
the device tier. Two open timer reads are tiered INSIDE the handler rather
than at the gate (``TIERED_INSIDE``, rule F1): the timer fire ledger is
read whole only by the tier the ``/ws/state`` handshake admits, and cut
down for anyone else.
"""

from __future__ import annotations

from typing import Any, Iterator, get_args

import pytest
from fastapi import FastAPI

from domovoi import admin_auth
from domovoi.auth import require_admin
from domovoi.main import app as core_app
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.main import app as web_app
from web.backend.schemas import ConversationTurn, Favorite, Memory, VoiceNote

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
    # ── web: more proxies the core decides (auth forwarded) ───────────
    # CORE-2: the core routes behind these are gated now (start takes an
    # admin Bearer, end the household token) and this hop forwards the
    # caller's credentials, so an ungated proxy cannot open a call.
    ("web", "POST", "/api/satellites/{room_id}/dropin/start"): "proxy — core require_admin_security gates it (auth forwarded; 501 before setup, CORE-14)",
    ("web", "POST", "/api/satellites/{room_id}/dropin/end"): "proxy — core require_device gates it (auth forwarded)",
    ("web", "POST", "/api/models/active"): (
        "proxy — the core's security tier gates the config write it forwards to"
    ),
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
    # CORE-14: an HTTP-opened drop-in is a live microphone; no pre-setup
    # grace for it (the phone socket's twin is in test_ws_gates).
    ("core", "POST", "/v1/admin/dropin/start"),
    ("core", "POST", "/v1/admin/device-token"),
    ("core", "POST", "/v1/admin/device-token/rotate"),
    # REV-32 (2026-10-08): approving a parked satellite binds a room to a
    # device for good — with strict pairing the default, no pre-setup grace,
    # or any LAN host could park its own device on an unclaimed core and
    # approve it. Reject takes the same tier.
    ("core", "POST", "/v1/admin/satellites/approvals/{room_id}/approve"),
    ("core", "POST", "/v1/admin/satellites/approvals/{room_id}/reject"),
    ("web", "POST", "/api/satellites/approvals/{room_id}/approve"),
    ("web", "POST", "/api/satellites/approvals/{room_id}/reject"),
    ("web", "PATCH", "/api/config/editable"),
    ("web", "POST", "/api/config/version/restart"),
    ("web", "POST", "/api/satellites/{room_id}/upgrade"),
    ("web", "POST", "/api/satellites/{room_id}/pairing/reset"),
    ("web", "POST", "/api/satellites/pending/{pending_id}/adopt"),
    ("web", "DELETE", "/api/satellites/{room_id}"),
    ("web", "POST", "/api/auth/device-token"),
    ("web", "POST", "/api/auth/device-token/rotate"),
    # Opt-in command recordings (owner decision 2026-09-28): turning a room
    # on or off, labelling and deleting what it kept. Only an admin, and
    # nobody before setup.
    ("web", "PUT", "/api/captures/rooms/{room_id}"),
    ("web", "DELETE", "/api/captures/rooms/{room_id}"),
    ("web", "PATCH", "/api/captures/clips/{room_id}/{capture_id}"),
    ("web", "DELETE", "/api/captures/clips/{room_id}/{capture_id}"),
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
        contexts = list(iter_route_contexts(app.routes))
        for rc in contexts:
            path = getattr(rc, "path", None)
            if path in ("/v1/admin/device-token", "/api/auth/device-token") and "GET" in (
                getattr(rc, "methods", None) or set()
            ):
                found[(label, path)] = list(_dependency_calls(getattr(rc, "dependant", None)))
    assert set(found) == {("core", "/v1/admin/device-token"), ("web", "/api/auth/device-token")}
    for key, calls in found.items():
        assert admin_auth.require_admin_security_read in calls, key


# ─── Household speech and personal content: paired devices only ──────────
#
# Owner decision 2026-09-26: what the household SAID, and what the house
# keeps about a PERSON, is read by a paired device — the household token or
# an admin session — and by nothing else on the LAN. Each GET below wears
# the READ half of the device tier, ``require_device_read``: the gate every
# other device-tier read takes (Documents, Files, Images, Videos), so the
# dashboard cookie and ``?device_token=`` pass exactly as they do there and
# a bare request is 401. None of them proxies a core read — each is the web
# process's own query (or a file under the config dir) — so there is no
# second hop to gate. The behaviour is driven end to end in
# test_web_speech_reads.py; this is the route-table half.

SPEECH_READS: list[tuple[str, str]] = [
    # conversation_log rows: user_text / assistant_text
    ("web", "/api/satellites/{room_id}/conversations"),
    ("web", "/api/people/{person_id}/conversations"),
    # voice notes: dictated speech
    ("web", "/api/satellites/{room_id}/notes"),
    ("web", "/api/people/{person_id}/notes"),
    # what the house keeps about a person (pending memories included)
    ("web", "/api/people/{person_id}/memories"),
    ("web", "/api/people/{person_id}/favorites"),
    ("web", "/api/people/{person_id}/preferences"),
    # the dashboard's text chat — titles and snippets are message text too
    ("web", "/api/chat/threads"),
    ("web", "/api/chat/threads/{thread_id}/messages"),
    ("web", "/api/chat/uploads/{token}"),
    # a recording of somebody in the house saying the wake phrase
    ("web", "/api/wake-words/{wake_word_id}/clips/{name}/audio"),
]

# Speech that was already gated one tier HIGHER before the decision: the
# satellite keeps a ``heard: <transcript>`` line for every utterance in the
# log ring these pull. They stay admin reads, at both hops.
SPEECH_READS_ADMIN: list[tuple[str, str]] = [
    ("core", "/v1/admin/satellite/{room_id}/logs"),
    ("web", "/api/satellites/{room_id}/logs"),
]

# Speech gated at the SECURITY tier: the opt-in command recordings (owner
# decision 2026-09-28) are raw audio of whoever spoke near a satellite an
# admin opted in, kept to tune end-of-turn detection. An admin reads them
# (Bearer, or the cookie for the listing and the audio), nobody before
# setup — and never the household token the reads above accept.
SPEECH_READS_SECURITY: list[tuple[str, str]] = [
    ("web", "/api/captures"),
    ("web", "/api/captures/clips/{room_id}/{capture_id}/audio"),
]

# Reads beside the list above that were deliberately LEFT OPEN — household
# state, or metadata without a word anybody said — pending an owner
# decision. Pinned open so that gating one is a recorded decision (here,
# in docs/SECURITY_PRIVACY.md and docs/API_REFERENCE.md), not a drive-by.
SPEECH_ADJACENT_LEFT_OPEN: dict[tuple[str, str], str] = {
    ("web", "/api/people/{person_id}/profiles"): "voice-profile enrolment metadata, never an embedding",
    ("web", "/api/satellites/{room_id}/timers"): (
        "household state: a room's countdowns; rule M1 — every reminder answers without its "
        "words (masked) unless the caller passes the device check"
    ),
    ("web", "/api/timers"): (
        "every room's rows at once — Home's countdowns (owner decision 2026-09-26) and the last "
        "10 minutes of fires; rule M1 — every reminder, row or fire, answers without its words "
        "unless the caller passes the device check; rule F1 — without it each fire is cut to "
        "what Home draws (no per-room outcomes, no who-stopped-it)"
    ),
    ("web", "/api/timers/fires"): (
        "tiered INSIDE the handler (rule F1, pinned by TIERED_INSIDE below): a household "
        "credential reads the whole 7-day ledger; anyone else the last 10 minutes only, each "
        "fire cut to what Home draws, reminder words masked (M1); the words spoken are never "
        "served"
    ),
    ("web", "/api/satellites/{room_id}/timer-announcements"): (
        "a room's \"Only reminders for this device\" flag — anyone in the house may see how a "
        "room behaves (the write is device tier)"
    ),
    ("web", "/api/news/people/{person_id}/topics"): "a person's followed topics — the nearest cousin of favorites",
    ("web", "/api/news/people/{person_id}/items"): "public stories fetched for a person's topics",
    ("web", "/api/news/people/{person_id}/briefing"): "a summary of those stories",
    ("web", "/api/wake-words/{wake_word_id}/clips"): "clip names, quality metrics, an amplitude envelope — no audio",
    ("web", "/api/denylist"): "opt-out rows: a date and an admin's note, never the embedding",
    ("web", "/api/acquisitions"): "the media request queue — search text or a URL",
}

# Household PRESENCE and the calendar: paired devices only too (owner
# decision 2026-10-08, WEB-15). The ``/ws/state`` handshake already needed
# a household credential because it pushes ``people.last_seen``, calendar
# titles and satellite details; these are the HTTP reads of the same state,
# which a LAN host could otherwise poll for the feed the socket withholds.
# The read half of the device tier, ``require_device_read``, exactly as the
# speech reads above take it.
HOUSEHOLD_STATE_READS: dict[tuple[str, str], str] = {
    ("web", "/api/people"): "the roster: names, last_seen_at (presence), the people.notes column",
    ("web", "/api/people/{person_id}"): "one roster row, with last_seen_at",
    ("web", "/api/people/{person_id}/sessions"): "when and in which room a person spoke",
    ("web", "/api/satellites"): "every room: presence, Wi-Fi SSID, hardware, code SHA, in_call_with",
    ("web", "/api/satellites/{room_id}"): "one room's row (the kiosk's label and idle mode)",
    ("web", "/api/satellites/pending"): "a satellite being adopted: MAC, board, model",
    ("web", "/api/satellites/{room_id}/sessions"): "a room's sessions, with the person who spoke",
    ("web", "/api/calendar/events"): "the household's appointments: titles, places, descriptions",
    ("web", "/api/calendar/events/{event_id}"): "one appointment",
    # REV-11 (2026-10-08): the siblings the first pass left open, each of
    # which still told a LAN poller when a room was in use.
    ("web", "/api/satellites/{room_id}/recently-played"): (
        "a room's play history, every play's started_at — when the room was in use"
    ),
    ("web", "/api/satellites/{room_id}/config"): (
        "whether the room's satellite is connected (200/404) and the mic, Wi-Fi and "
        "audio settings it reported; proxies the core read below"
    ),
    ("core", "/v1/admin/satellite/{room_id}/config"): "the core half of the read above",
    ("core", "/v1/stats/latency"): (
        "per-stage voice-turn timings — numbers only, but a turn count for one room "
        "over a short window says somebody just spoke there"
    ),
    ("web", "/api/stats/latency"): "proxy of the core read above (credential forwarded)",
    # SAT-HEALTH (2026-10-09): a room's health sample says whether a turn,
    # chat or call is open right now (`idle`), its SSID and gateway, and the
    # outage report what its previous life saw.
    ("core", "/v1/admin/satellite/{room_id}/health"): (
        "a room's latest health sample, 24 h summary and outage reports"
    ),
    ("web", "/api/satellites/{room_id}/health"): "proxy of the core read above (credential forwarded)",
}


@pytest.mark.parametrize(
    ("label", "path"), sorted(HOUSEHOLD_STATE_READS),
    ids=[f"{a} GET {p}" for a, p in sorted(HOUSEHOLD_STATE_READS)],
)
def test_presence_and_calendar_reads_need_a_paired_device(label, path) -> None:
    assert HOUSEHOLD_STATE_READS[(label, path)].strip()
    assert (label, path) not in SPEECH_ADJACENT_LEFT_OPEN
    calls = _get_gates((label, path))
    assert admin_auth.require_device_read in calls, (
        f"{label} GET {path} publishes household presence or the calendar — it "
        f"must depend on require_device_read (paired devices only)"
    )


# The response models that ARE household speech or personal content. Any
# GET on either app answering with one of them must be on SPEECH_READS (or
# SPEECH_READS_ADMIN): a new route serving ConversationTurn rows from
# somewhere else fails here instead of quietly reopening the history.
SPEECH_MODELS = (ConversationTurn, VoiceNote, Memory, Favorite)


def _get_records(app: FastAPI, label: str) -> dict[tuple[str, str], Any]:
    """``(app, path) -> route context`` for every GET route, walked the same
    way as the mutations above."""
    out: dict[tuple[str, str], Any] = {}
    for rc in iter_route_contexts(app.routes):
        path = getattr(rc, "path", None)
        if path and "GET" in (getattr(rc, "methods", None) or set()):
            out[(label, path)] = rc
    return out


GET_ROUTES = {**_get_records(core_app, "core"), **_get_records(web_app, "web")}


def _get_gates(key: tuple[str, str]) -> list[Any]:
    assert key in GET_ROUTES, f"GET {key[1]} is missing from the {key[0]} app"
    return list(_dependency_calls(getattr(GET_ROUTES[key], "dependant", None)))


@pytest.mark.parametrize(
    ("label", "path"), SPEECH_READS, ids=[f"{a} GET {p}" for a, p in SPEECH_READS]
)
def test_speech_read_needs_a_paired_device(label, path) -> None:
    calls = _get_gates((label, path))
    assert admin_auth.require_device_read in calls, (
        f"{label} GET {path} returns household speech or personal content — it "
        f"must depend on require_device_read (paired devices only)"
    )


@pytest.mark.parametrize(
    ("label", "path"), SPEECH_READS_ADMIN,
    ids=[f"{a} GET {p}" for a, p in SPEECH_READS_ADMIN],
)
def test_the_log_pull_stays_an_admin_read(label, path) -> None:
    calls = _get_gates((label, path))
    assert admin_auth.require_admin_read in calls
    assert admin_auth.require_device_read not in calls, (
        f"{label} GET {path} must not drop to the household tier"
    )


@pytest.mark.parametrize(
    ("label", "path"), SPEECH_READS_SECURITY,
    ids=[f"{a} GET {p}" for a, p in SPEECH_READS_SECURITY],
)
def test_command_recordings_are_security_tier_reads(label, path) -> None:
    calls = _get_gates((label, path))
    assert admin_auth.require_admin_security_read in calls, (
        f"{label} GET {path} serves command recordings — it must depend on "
        f"require_admin_security_read (admin only, 501 before setup)"
    )
    for weaker in (admin_auth.require_device_read, admin_auth.require_device,
                   admin_auth.require_admin_read):
        assert weaker not in calls, f"{label} GET {path} must not drop to {weaker.__name__}"


# Lyrics (V021, owner decision 2026-10-03): the household's own copy of
# copyrighted text for its songs, readable by the household tier only and
# never by an open page — unlike the library, now-playing and cover reads
# beside them, which stay open and carry no lyrics (lyrics contract [A1]).
LYRICS_READS: list[tuple[str, str]] = [
    ("web", "/api/music/library/{track_id}/lyrics"),
    ("web", "/api/music/now-playing/{room_id}/lyrics"),
    ("web", "/api/music/lyrics/status"),
]


@pytest.mark.parametrize(
    ("label", "path"), LYRICS_READS, ids=[f"{a} GET {p}" for a, p in LYRICS_READS]
)
def test_lyrics_reads_take_the_household_tier(label, path) -> None:
    calls = _get_gates((label, path))
    assert admin_auth.require_device_read in calls, (
        f"{label} GET {path} serves lyrics — it must depend on require_device_read "
        f"(the household tier), never answer an open page"
    )


@pytest.mark.parametrize(
    ("label", "path"), sorted(SPEECH_ADJACENT_LEFT_OPEN),
    ids=[f"{a} GET {p}" for a, p in sorted(SPEECH_ADJACENT_LEFT_OPEN)],
)
def test_the_reads_left_open_are_still_open(label, path) -> None:
    """Left open on purpose, pending an owner decision. If one of these
    grows a gate, move it to SPEECH_READS and say so in the docs."""
    assert SPEECH_ADJACENT_LEFT_OPEN[(label, path)].strip()
    calls = _get_gates((label, path))
    gates = {
        admin_auth.require_device_read, admin_auth.require_device,
        admin_auth.require_admin_read, admin_auth.require_admin_mutation,
    }
    assert not gates.intersection(calls), f"{label} GET {path} is gated now"


# Open at the gate, tiered inside the handler: the route answers anyone,
# but HOW MUCH depends on the caller's device check. Rule F1 (2026-09-30):
# the timer fire ledger (V018) is 7 days of when every timer and reminder
# went off, each room's outcome and reason code (``in_call``,
# ``recording``, ``capturing``) and which room said "stop the timer" and
# when. Read whole only by the tier ``/ws/state`` admits (whose
# ``timer_fires`` push carries it whole); anyone else reads the last 10
# minutes, each fire cut to what Home's done line and the alert card
# draw. Pinned from both sides: the handler's tier here, the behaviour in
# domovoi/tests/test_web_timer_fires.py (every tier, every field).
TIERED_INSIDE: dict[tuple[str, str], str] = {
    ("web", "/api/timers/fires"): "the whole ledger to a household credential, 10 minutes cut down to anyone else",
    ("web", "/api/timers"): "its `fires` cut down for a caller without a household credential",
}

# Every result the device check can give, and whether it reads the whole
# fire ledger. The household token, an admin Bearer, the dashboard cookie
# and the pre-setup grace do (so the Android app, a paired shared screen and
# a signed-in dashboard); nothing, a stale token and a throttled source do
# not.
_FIRE_LEDGER_BY_RESULT = {
    "ok": True, "admin": True, "pre-setup": True, "cookie-only": True,
    "no-auth": False, "invalid": False, "throttled": False,
}


def test_every_device_check_result_is_placed_for_the_fire_ledger() -> None:
    assert set(_FIRE_LEDGER_BY_RESULT) == set(get_args(admin_auth.DeviceCheckResult))


@pytest.mark.parametrize(("label", "path"), sorted(TIERED_INSIDE),
                         ids=[f"{a} GET {p}" for a, p in sorted(TIERED_INSIDE)])
def test_the_fire_ledger_reads_are_open_at_the_gate(label, path) -> None:
    assert TIERED_INSIDE[(label, path)].strip()
    assert (label, path) in SPEECH_ADJACENT_LEFT_OPEN
    calls = _get_gates((label, path))
    gates = {
        admin_auth.require_device_read, admin_auth.require_device,
        admin_auth.require_admin_read, admin_auth.require_admin_mutation,
        admin_auth.require_admin_security_read,
    }
    assert not gates.intersection(calls), f"{label} GET {path} is gated now"


def test_the_fire_ledger_tier_is_the_state_socket_s(monkeypatch) -> None:
    """The handler reads whole for exactly the device-check results the
    ``/ws/state`` handshake admits — so the ``timer_fires`` push never
    reaches a caller the HTTP read would cut down, and the other way round."""
    import asyncio

    from web.backend import main as web_main
    from web.backend.api import satellites as sat_api

    handler = {r: r in sat_api._READS_FIRE_LEDGER for r in _FIRE_LEDGER_BY_RESULT}
    assert handler == _FIRE_LEDGER_BY_RESULT

    class _Ws:
        headers: dict[str, str] = {}

    async def admitted(result: str) -> bool:
        async def check(_conn, *a, **kw):
            return result
        monkeypatch.setattr(web_main.admin_auth, "check_device_request", check)
        return await web_main._authorize_state_socket(_Ws()) is not web_main._REFUSE

    socket = {r: asyncio.run(admitted(r)) for r in _FIRE_LEDGER_BY_RESULT}
    assert socket == _FIRE_LEDGER_BY_RESULT


def _answers_with(rc: Any, models: tuple[type, ...]) -> bool:
    """True when the route's response model is, or contains (``list[...]``,
    ``Optional[...]``), one of ``models``."""
    stack = [getattr(rc, "response_model", None)]
    while stack:
        t = stack.pop()
        if t is None:
            continue
        if isinstance(t, type) and issubclass(t, models):
            return True
        stack.extend(get_args(t))
    return False


def test_every_route_serving_speech_models_is_on_the_list() -> None:
    serving = {
        key for key, rc in GET_ROUTES.items() if _answers_with(rc, SPEECH_MODELS)
    }
    # Guard against the walk going blind (the parametrized tests above would
    # still pass on an empty table): the known routes must be found.
    assert ("web", "/api/people/{person_id}/conversations") in serving
    assert ("web", "/api/satellites/{room_id}/notes") in serving
    listed = set(SPEECH_READS) | set(SPEECH_READS_ADMIN)
    missing = sorted(serving - listed)
    assert not missing, (
        f"these GETs answer with household speech / personal-content models but "
        f"are not on SPEECH_READS — gate them with require_device_read: {missing}"
    )


# ─── Plugin routes: which tier each bundled-radio mutation answers to ─────
#
# Plugin routers are mounted at RUNTIME, so they are not in either app's
# static route table above. Both processes put them behind one gate body,
# ``webkit.enforce_route_tier``, which reads the tier off the route
# function: ``@device_endpoint`` (household token or admin Bearer —
# ``require_device``), ``@open_endpoint`` (nothing) or the admin default
# (``admin_required``). ``webkit.endpoint_tier`` is that same predicate,
# so walking the bundled radio plugin's REAL routers with it is walking
# what the gate will do. Moving a route between tiers is a deliberate
# edit here AND in docs/SECURITY_PRIVACY.md / docs/API_REFERENCE.md.

RADIO_ROUTE_TIERS: dict[tuple[str, str, str], str] = {
    # Listening is a household action (2026-09-25): the nearest core
    # precedents — podcast subscribe / unsubscribe, news feed attach /
    # detach, playlist delete — are all device tier.
    ("web", "POST", "/play"): "device",
    ("web", "POST", "/stations"): "device",
    ("web", "PATCH", "/stations/{station_id}"): "device",
    ("web", "DELETE", "/stations/{station_id}"): "device",
    ("web", "POST", "/stations/{station_id}/resolve-simulcast"): "device",
    ("core", "POST", "/stations/{station_id}/resolve-simulcast"): "device",
    # A long server-side job, like the core's library sweeps.
    ("web", "POST", "/fcc-import"): "admin",
    ("core", "POST", "/fcc-import"): "admin",
}


def _radio_mutations() -> dict[tuple[str, str, str], Any]:
    """``(process, METHOD, path) -> endpoint`` for every mutating route on
    the radio plugin's web and core routers, walked the way the load-time
    checks walk them (``webkit.iter_plugin_routes`` — a router included
    INTO a plugin router stays nested on FastAPI >= 0.139)."""
    import sys
    from pathlib import Path

    radio_dir = Path(__file__).resolve().parents[2] / "plugins" / "radio"
    if str(radio_dir) not in sys.path:
        sys.path.insert(0, str(radio_dir))
    from domovoi_plugin_radio import core as radio_core
    from domovoi_plugin_radio import web as radio_web

    class _Ctx:  # build_router only captures these; nothing is called
        db_session_scope = None
        core = None

    routers = {
        "web": radio_web.build_router(_Ctx()),
        "core": radio_core._build_core_router(None),
    }
    from domovoi.webkit import iter_plugin_routes

    out: dict[tuple[str, str, str], Any] = {}
    for process, router in routers.items():
        for route in iter_plugin_routes([router]):
            for method in MUTATING_METHODS:
                if method in (getattr(route, "methods", None) or set()):
                    out[(process, method, route.path)] = route.endpoint
    return out


def test_every_radio_mutation_sits_on_its_declared_tier() -> None:
    from domovoi.webkit import endpoint_tier

    found = {key: endpoint_tier(fn) for key, fn in _radio_mutations().items()}
    assert set(found) == set(RADIO_ROUTE_TIERS), (
        "radio's mutating routes changed — give each new one a tier in "
        f"RADIO_ROUTE_TIERS: {sorted(set(found) ^ set(RADIO_ROUTE_TIERS))}"
    )
    wrong = {k: (found[k], want) for k, want in RADIO_ROUTE_TIERS.items() if found[k] != want}
    assert not wrong, f"(found, declared) tier mismatch: {wrong}"


def test_radio_opens_nothing_to_the_whole_lan() -> None:
    """The device tier is the floor for radio: no route answers a caller
    who holds no credential at all."""
    from domovoi.webkit import is_open_endpoint

    opened = [k for k, fn in _radio_mutations().items() if is_open_endpoint(fn)]
    assert opened == []
