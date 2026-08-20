"""The access-control surface.

``resolve_route`` is the single decision that stands between a remote
device and the household's LAN, and it is a pure function precisely so
it can be enumerated exhaustively here — no network, no database, no
plugin runtime.

If a change to the allowlist breaks one of these, that is the test
working. Widening what remote access reaches should be a deliberate,
visible edit to this file, not a side effect.
"""

from __future__ import annotations

import pytest

from domovoi_plugin_ltl_remote import framing
from domovoi_plugin_ltl_remote.local_proxy import (
    Allowed,
    Denied,
    OriginError,
    REMOTE_MARKER_HEADER,
    resolve_route,
    sanitize_request_headers,
    sanitize_response_headers,
    validate_origin,
)

from conftest import FakeSettings


def allow(path, method="GET", **overrides):
    return resolve_route(method, path, FakeSettings(**overrides))


# ─── what is reachable ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,origin",
    [
        ("/api/satellites", "dashboard"),
        ("/api/music/library?limit=50", "dashboard"),
        ("/ws/state", "dashboard"),
        ("/media/track/12.mp3", "dashboard"),
        ("/plugins/radio/static/stations.jsx", "dashboard"),
        ("/static/components.jsx", "dashboard"),
        ("/assets/domovoi.svg", "dashboard"),
        ("/v1/admin/announce", "core"),
        ("/v1/stream/kitchen", "core"),
    ],
)
def test_allowlisted_paths_resolve_to_the_right_origin(path, origin):
    decision = allow(path)
    assert isinstance(decision, Allowed), decision
    assert decision.route.origin == origin
    assert decision.path == path


def test_the_more_specific_prefix_wins():
    """``/v1/stream/`` is a WebSocket route and sits inside ``/v1/``;
    order in the allowlist decides, so it is worth pinning."""
    streamed = allow("/v1/stream/kitchen")
    plain = allow("/v1/admin/announce")
    assert streamed.route.websocket is True
    assert plain.route.websocket is False


def test_query_strings_do_not_affect_matching():
    decision = allow("/api/music/library?q=..%2F..%2Fetc")
    assert isinstance(decision, Allowed)
    # The original string is forwarded verbatim: decoding and re-encoding
    # would change the request the client actually made.
    assert decision.path.endswith("?q=..%2F..%2Fetc")


# ─── what is not ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/admin",
        "/etc/passwd",
        "/v2/anything",
        "/apixyz/sneaky",          # prefix is "/api/", not "/api"
        "/wsstate",
        "/mediafiles/x",
    ],
)
def test_paths_outside_the_allowlist_are_refused(path):
    decision = allow(path)
    assert isinstance(decision, Denied)
    assert decision.code == framing.ERR_PATH_NOT_ALLOWED


@pytest.mark.parametrize(
    "path",
    [
        "/api/../../etc/passwd",
        "/api/..%2f..%2fetc/passwd",
        "/api/%2e%2e/%2e%2e/etc/passwd",
        "/api/%2E%2E/secrets",
        "//evil.example.com/api/x",       # protocol-relative
        "api/no-leading-slash",
        "/api/nul\x00byte",
        "/api\\windows\\style",
    ],
)
def test_path_traversal_and_smuggling_are_refused(path):
    """Traversal is checked on a percent-decoded copy so encoded dots
    cannot walk out of the prefix that was matched."""
    decision = allow(path)
    assert isinstance(decision, Denied)


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "", "get post", None])
def test_unknown_methods_are_refused(method):
    decision = resolve_route(method, "/api/satellites", FakeSettings())
    assert isinstance(decision, Denied)
    assert decision.code == framing.ERR_PROTOCOL


@pytest.mark.parametrize("path", ["", None, 12])
def test_missing_paths_are_refused(path):
    assert isinstance(resolve_route("GET", path, FakeSettings()), Denied)


# ─── the switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_read_only_mode_drops_mutations(method):
    decision = allow("/api/satellites", method=method, read_only=True)
    assert isinstance(decision, Denied)
    assert "read-only" in decision.message


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_read_only_mode_still_allows_reads(method):
    assert isinstance(allow("/api/satellites", method=method, read_only=True), Allowed)


def test_turning_off_the_core_api_closes_remote_code_execution():
    """``/v1/**`` reaches Domovoi's admin tier, which includes plugin
    installation. This switch is the documented way to take remote code
    execution off the table without giving up the dashboard."""
    assert isinstance(allow("/v1/admin/announce", allow_core_admin=False), Denied)
    assert isinstance(allow("/v1/stream/kitchen", allow_core_admin=False), Denied)
    # The dashboard keeps working.
    assert isinstance(allow("/api/satellites", allow_core_admin=False), Allowed)


def test_turning_off_media_streaming_closes_only_media():
    assert isinstance(allow("/media/track/1.mp3", allow_media_streaming=False), Denied)
    assert isinstance(allow("/api/music/library", allow_media_streaming=False), Allowed)


# ─── origins ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:6369",
        "http://localhost:6370",
        "http://192.168.1.10:6369",
        "http://10.0.0.4:6370",
        "https://172.16.9.9:6369",
    ],
)
def test_private_origins_are_accepted(url):
    assert validate_origin(url, label="origin").startswith(("http://", "https://"))


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:6369",              # public address
        "http://example.com:6369",          # a name can be repointed
        "ftp://127.0.0.1",                  # wrong scheme
        "127.0.0.1:6369",                   # no scheme
        "http://127.0.0.1:6369/nested",     # not a bare origin
        "http://127.0.0.1:6369?a=b",
        "",
    ],
)
def test_unsafe_origins_are_refused(url):
    """A public origin here would turn a household's Domovoi server into
    an open proxy onto the internet. It is refused at load time so a
    typo cannot do it."""
    with pytest.raises(OriginError):
        validate_origin(url, label="origin")


# ─── headers ─────────────────────────────────────────────────────────────


def test_request_headers_drop_network_position_claims():
    """Domovoi rate-limits per source IP. Forwarding a client's claimed
    address would let a device with a rotating IP walk around those
    limits and would poison core's view of who is talking to it."""
    cleaned = sanitize_request_headers({
        "Authorization": "Bearer abc",
        "X-Forwarded-For": "1.2.3.4",
        "x-real-ip": "1.2.3.4",
        "Forwarded": "for=1.2.3.4",
        "X-Domovoi-Remote": "spoofed",
        "Connection": "keep-alive",
        "Host": "evil.example.com",
        "Content-Type": "application/json",
    })
    assert cleaned["Authorization"] == "Bearer abc"      # end-to-end, untouched
    assert cleaned["Content-Type"] == "application/json"
    for gone in ("X-Forwarded-For", "x-real-ip", "Forwarded", "Connection", "Host"):
        assert gone not in cleaned
    # The marker is set by us, never accepted from the peer.
    assert cleaned[REMOTE_MARKER_HEADER] == "ltl"


def test_response_headers_drop_hop_by_hop():
    cleaned = sanitize_response_headers({
        "Content-Type": "application/json",
        "Transfer-Encoding": "chunked",
        "Connection": "close",
        "Content-Length": "12",
    })
    assert cleaned == {"Content-Type": "application/json"}


def test_response_cookies_lose_their_domain_attribute():
    """A Domovoi session cookie must stay scoped to whatever origin the
    client is actually talking to."""
    cleaned = sanitize_response_headers({
        "Set-Cookie": "domovoi_session=abc; Path=/; Domain=hearth.lan; HttpOnly",
    })
    value = cleaned["Set-Cookie"]
    assert "Domain=" not in value
    assert "domovoi_session=abc" in value and "HttpOnly" in value


def test_header_sanitizers_tolerate_empty_input():
    assert sanitize_request_headers({})[REMOTE_MARKER_HEADER] == "ltl"
    assert sanitize_request_headers(None)[REMOTE_MARKER_HEADER] == "ltl"
    assert sanitize_response_headers(None) == {}
