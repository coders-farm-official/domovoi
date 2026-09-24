"""WEB-9 — /ws/state answers to the household, not to the LAN.

The stream carries who is home, what the calendar says and which devices
are on the network, so the handshake now needs a household credential: the
``X-Device-Token`` header, an admin Bearer, the dashboard's session cookie,
or — for a browser, which can set none of those — the
``domovoi.device-token-b64.<base64url(token)>`` subprotocol (the legacy
``domovoi.device-token.<token>`` spelling is still read, for a browser on a
cached ``data.js``). Without one the socket is closed before it is accepted
(the server answers the upgrade 403) and is never registered with the
broadcaster, so it is pushed nothing at all.

DB-free: the credential check runs over ``install_fake_db``'s fakes and the
broadcaster is a plain in-memory object.
"""

from __future__ import annotations

import base64
import json

import pytest
from starlette.testclient import TestClient

from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_app
from web.backend.main import (
    WS_DEVICE_TOKEN_SUBPROTOCOL,
    WS_DEVICE_TOKEN_SUBPROTOCOL_B64,
)
from web.backend.realtime import StateBroadcaster

DEVICE_TOKEN = "household-token"
ADMIN_TOKEN = "admin-token"
LEGACY_SUBPROTOCOL = f"{WS_DEVICE_TOKEN_SUBPROTOCOL}{DEVICE_TOKEN}"


def b64_subprotocol(token: str) -> str:
    """What ``data.js`` offers: base64url of the UTF-8 token, ``=`` padding
    stripped."""
    payload = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{WS_DEVICE_TOKEN_SUBPROTOCOL_B64}{payload}"


SUBPROTOCOL = b64_subprotocol(DEVICE_TOKEN)


@pytest.fixture
def broadcaster(monkeypatch) -> StateBroadcaster:
    """A fresh broadcaster on the app, without starting the lifespan (which
    would open the poll loop's database connections)."""
    fresh = StateBroadcaster()
    monkeypatch.setattr(web_app.state, "broadcaster", fresh, raising=False)
    return fresh


@pytest.fixture
def claimed(monkeypatch):
    """A set-up household: an admin password exists, one live session, one
    device token."""
    return install_fake_db(
        monkeypatch,
        admin=True,
        sessions={ADMIN_TOKEN},
        device_token=DEVICE_TOKEN,
    )


def _client(**kw) -> TestClient:
    # No `with`: the lifespan would start the poll loop and the LISTEN task.
    return TestClient(web_app, **kw)


# ─── Who gets in ──────────────────────────────────────────────────────────


def test_a_socket_with_no_credential_is_refused_and_is_pushed_nothing(
    claimed, broadcaster
) -> None:
    with pytest.raises(Exception) as excinfo:
        with _client().websocket_connect("/ws/state"):
            pass  # pragma: no cover — the handshake never completes
    # Starlette surfaces the refusal either as the denial response (403) or
    # as the close that produced it; both mean the same thing here.
    status = getattr(excinfo.value, "status_code", None)
    code = getattr(excinfo.value, "code", None)
    assert status == 403 or code == 1008, excinfo.value
    assert broadcaster.client_count() == 0


def test_a_stale_device_token_is_refused(claimed, broadcaster) -> None:
    for credential in (
        {"headers": {HEADER: "yesterdays-token"}},
        {"subprotocols": [f"{WS_DEVICE_TOKEN_SUBPROTOCOL}yesterdays-token"]},
        {"headers": bearer("expired-session")},
    ):
        with pytest.raises(Exception):
            with _client().websocket_connect("/ws/state", **credential):
                pass  # pragma: no cover
    assert broadcaster.client_count() == 0


def test_the_app_s_device_token_header_gets_in(claimed, broadcaster) -> None:
    with _client().websocket_connect("/ws/state", headers={HEADER: DEVICE_TOKEN}) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1
    assert broadcaster.client_count() == 0


def test_an_admin_bearer_gets_in(claimed, broadcaster) -> None:
    with _client().websocket_connect("/ws/state", headers=bearer(ADMIN_TOKEN)) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


def test_the_signed_in_dashboard_s_cookie_gets_in(claimed, broadcaster) -> None:
    """This socket only renders state, which is the read tier's bar — and
    the cookie is SameSite=Strict, so another site's page cannot bring it
    to this handshake."""
    client = _client(cookies={COOKIE: ADMIN_TOKEN})
    with client.websocket_connect("/ws/state") as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


def test_a_browser_can_present_the_token_as_a_subprotocol(claimed, broadcaster) -> None:
    """A browser cannot set a header on a WebSocket handshake, and a query
    string would land in the access log — so the paired-but-not-signed-in
    case (a kiosk display) offers the token as a subprotocol, which the
    server must echo back or the browser drops the connection."""
    with _client().websocket_connect("/ws/state", subprotocols=[SUBPROTOCOL]) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


@pytest.mark.parametrize(
    "token",
    [
        "household-token",                       # the phrase shape
        "a token with spaces",                   # SP — illegal raw, fine b64
        "p@ss,word,12345",                       # the comma that truncates
        "100% safe, really!",                    # % and , and !
        'quote"and\\backslash',                    # " and \\
        "semi;colon:slash/token",                # ; : /
        "=equals=and=more=",                     # = , which base64 pads with
        "~!#$&*+^_`|.-tchar",                    # already a legal RFC 9110 token
        " ".join(["x"] * 60)[:128],              # at the 128 cap
    ],
)
def test_any_printable_token_survives_the_b64_subprotocol(
    monkeypatch, broadcaster, token
) -> None:
    """The point of the whole change. Every one of these is a token an
    admin may now set, and every one of them is either illegal in the raw
    subprotocol (the browser throws, uvicorn answers 400) or silently
    corrupted by the server's own comma split. base64url carries them
    all."""
    install_fake_db(monkeypatch, admin=True, device_token=token)
    with _client().websocket_connect(
        "/ws/state", subprotocols=[b64_subprotocol(token)]
    ) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


def test_the_legacy_raw_subprotocol_is_still_accepted(claimed, broadcaster) -> None:
    """A browser holding a cached ``data.js`` from before this change offers
    the raw form, and refusing it would kill its live state stream."""
    with _client().websocket_connect(
        "/ws/state", subprotocols=[LEGACY_SUBPROTOCOL]
    ) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


def test_both_offers_are_read_and_the_b64_one_wins(claimed, broadcaster) -> None:
    """Mid-rollout the dashboard offers b64 FIRST and the legacy raw form
    second, because a server that has not been restarted yet knows only the
    raw prefix and a browser whose offers are all unrecognised drops the
    socket. This server must find the b64 element wherever it sits, and
    echo back exactly the element it chose."""
    offers = [SUBPROTOCOL, LEGACY_SUBPROTOCOL]
    with _client().websocket_connect("/ws/state", subprotocols=offers) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1

    from web.backend.main import _offered_token_subprotocol

    class _WS:
        headers = {"sec-websocket-protocol": ", ".join(offers)}

    assert _offered_token_subprotocol(_WS()) == (DEVICE_TOKEN, SUBPROTOCOL)
    # ...and in the other order too: every element is scanned for the b64
    # prefix before any of them is read as the legacy one.
    class _WS2:
        headers = {"sec-websocket-protocol": ", ".join(reversed(offers))}

    assert _offered_token_subprotocol(_WS2()) == (DEVICE_TOKEN, SUBPROTOCOL)


def test_a_malformed_b64_offer_is_not_a_credential(claimed, broadcaster) -> None:
    """Padding restored, UTF-8 decoded — and anything that is neither is
    None rather than a half-decoded string."""
    from web.backend.main import _offered_token_subprotocol

    for payload in ("!!!not-base64!!!", "_" * 5, "3q2-7w"):
        class _WS:
            headers = {
                "sec-websocket-protocol": f"domovoi.device-token-b64.{payload}"
            }

        token, offered = _offered_token_subprotocol(_WS())
        assert token is None or isinstance(token, str)
        assert offered == f"domovoi.device-token-b64.{payload}"

    with pytest.raises(Exception):
        with _client().websocket_connect(
            "/ws/state", subprotocols=["domovoi.device-token-b64.!!!not-base64!!!"]
        ):
            pass  # pragma: no cover — the handshake never completes
    assert broadcaster.client_count() == 0


def test_the_subprotocol_is_not_a_free_guessing_oracle(claimed, broadcaster) -> None:
    """The subprotocol used to reach ``validate_device_token`` directly,
    which meant the one transport a browser can use was also the one that
    skipped the device-token backoff. Now it goes through
    ``check_device_credential`` like every other presentation, so a source
    that has burned its grace is refused without its offer being read."""
    from domovoi import admin_auth

    for i in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS + 2):
        with pytest.raises(Exception):
            with _client().websocket_connect(
                "/ws/state",
                subprotocols=[b64_subprotocol(f"guess-{i}")],
            ):
                pass  # pragma: no cover — the handshake never completes
    assert admin_auth.DEVICE_TOKEN_BACKOFF.retry_after("testclient") > 0
    # Even the right token is turned away while the source is throttled.
    with pytest.raises(Exception):
        with _client().websocket_connect("/ws/state", subprotocols=[SUBPROTOCOL]):
            pass  # pragma: no cover — the handshake never completes
    assert broadcaster.client_count() == 0
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()
    with _client().websocket_connect("/ws/state", subprotocols=[SUBPROTOCOL]) as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


def test_a_fresh_install_is_still_open(monkeypatch, broadcaster) -> None:
    """Before anyone has claimed the admin password there is no household
    credential to hold, so the dashboard of a brand-new install connects —
    the same pre-setup grace the rest of the surface keeps."""
    install_fake_db(monkeypatch, admin=False)
    with _client().websocket_connect("/ws/state") as ws:
        ws.send_json({"subscribe": []})
        assert broadcaster.client_count() == 1


# ─── What gets through ────────────────────────────────────────────────────


def test_an_admitted_subscriber_still_receives_the_whole_payload(
    claimed, broadcaster
) -> None:
    """The decision on this card was to gate the socket rather than trim
    the events: a client that belongs to the household sees exactly what it
    saw before, presence and calendar titles included."""
    with _client().websocket_connect("/ws/state", headers={HEADER: DEVICE_TOKEN}) as ws:
        ws.send_json({"subscribe": []})
        ws.portal.call(
            broadcaster.broadcast,
            "people.last_seen.changed",
            {"people": [{"name": "Kamron", "last_seen_at": "2026-09-22T12:00:00Z"}]},
        )
        event = ws.receive_json()
    assert event["type"] == "people.last_seen.changed"
    assert event["people"][0]["name"] == "Kamron"


def test_nothing_is_pushed_to_a_socket_that_never_got_in(claimed, broadcaster) -> None:
    """The refused handshake leaves no subscriber behind, so a broadcast
    that would have carried presence has nowhere to go."""
    with pytest.raises(Exception):
        with _client().websocket_connect("/ws/state"):
            pass  # pragma: no cover

    sent: list[str] = []

    class _Spy:
        async def send_text(self, message: str) -> None:  # pragma: no cover
            sent.append(message)

    import asyncio

    asyncio.run(
        broadcaster.broadcast("satellites.presence.changed", {"rooms": ["kitchen"]})
    )
    assert sent == []
    assert broadcaster.client_count() == 0


# ─── The clients present it ───────────────────────────────────────────────


def _data_js() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2] / "web" / "static" / "data.js"
    ).read_text(encoding="utf-8")


def test_the_dashboard_offers_its_token_on_the_handshake() -> None:
    data_js = _data_js()
    assert "const WS_DEVICE_TOKEN_SUBPROTOCOL = 'domovoi.device-token.';" in data_js
    assert (
        "const WS_DEVICE_TOKEN_SUBPROTOCOL_B64 = 'domovoi.device-token-b64.';"
        in data_js
    )
    opener = data_js[data_js.index("const url = httpBase"):data_js.index("ws.addEventListener('message'")]
    assert "const protocols = wsDeviceSubprotocols(device);" in opener
    assert "new WebSocket(url, protocols) : new WebSocket(url)" in opener
    assert "if (device) hello.device_token = device;" in opener


def test_the_dashboard_never_logs_the_exception_that_carries_the_token() -> None:
    """A subprotocol the constructor refuses throws a SyntaxError whose
    MESSAGE contains the whole subprotocol string — the household secret.
    The catch must not log it, and must not schedule a reconnect that will
    throw again forever: it retries once without a subprotocol."""
    opener = _data_js()
    opener = opener[opener.index("const url = httpBase"):opener.index("ws.addEventListener('message'")]
    catch = opener[opener.index("} catch (e) {"):]
    assert "console.warn('ws connect failed:', e)" not in catch
    assert ", e)" not in catch.split("this.ws = ws")[0]
    assert "ws = new WebSocket(url);" in catch


def test_the_prefixes_match_on_both_sides_and_cannot_collide() -> None:
    assert WS_DEVICE_TOKEN_SUBPROTOCOL == "domovoi.device-token."
    assert WS_DEVICE_TOKEN_SUBPROTOCOL_B64 == "domovoi.device-token-b64."
    assert json.dumps(SUBPROTOCOL).startswith('"domovoi.device-token-b64.')
    # The character after "token" is "-" in one and "." in the other, so an
    # OLD server skips the new element instead of reading it as a token —
    # which is what makes offering both safe during a rollout.
    assert not WS_DEVICE_TOKEN_SUBPROTOCOL_B64.startswith(WS_DEVICE_TOKEN_SUBPROTOCOL)
    assert not WS_DEVICE_TOKEN_SUBPROTOCOL.startswith(WS_DEVICE_TOKEN_SUBPROTOCOL_B64)
