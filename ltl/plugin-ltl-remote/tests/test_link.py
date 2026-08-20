"""The link layer: stream limiting, and a full sealed request round trip.

``StreamLimiter`` is pure and runs anywhere. The ``ClientLink`` tests
need Domovoi (the module imports ``LongRunWorker`` from the SDK), so
they skip in a bare checkout — but when they do run they exercise the
whole path a real remote request takes: handshake, seal, allowlist,
forward, response frames, access log.
"""

from __future__ import annotations

import asyncio

import pytest

from domovoi_plugin_ltl_remote import crypto, framing
from domovoi_plugin_ltl_remote.local_proxy import Allowed, Denied, StreamLimiter

from conftest import FakeSettings, needs_domovoi


# ─── StreamLimiter (pure) ────────────────────────────────────────────────


def test_limiter_reports_room_and_counts_in_flight():
    limiter = StreamLimiter(2)
    assert limiter.has_room and limiter.in_flight == 0
    with limiter:
        assert limiter.in_flight == 1 and limiter.has_room
        with limiter:
            assert limiter.in_flight == 2
            assert not limiter.has_room
    assert limiter.in_flight == 0


def test_limiter_refuses_rather_than_queues():
    """A semaphore would make an over-limit request *wait*, which reads
    to a remote user as a hang. Refusing immediately lets the client be
    told what happened."""
    limiter = StreamLimiter(1)
    with limiter:
        with pytest.raises(RuntimeError):
            limiter.__enter__()


def test_limiter_releases_on_exception():
    limiter = StreamLimiter(1)
    with pytest.raises(ValueError):
        with limiter:
            raise ValueError("boom")
    assert limiter.in_flight == 0 and limiter.has_room


def test_limiter_floors_at_one():
    assert StreamLimiter(0).limit == 1
    assert StreamLimiter(-5).limit == 1


def test_limiter_never_goes_negative():
    limiter = StreamLimiter(2)
    limiter.__exit__()
    assert limiter.in_flight == 0


# ─── ClientLink (needs Domovoi) ──────────────────────────────────────────


class FakeProxy:
    """Stands in for the loopback forwarder. Records what it was asked
    to fetch and replays a scripted response."""

    def __init__(self, status=200, chunks=(b"hello",), fail=False):
        self.status = status
        self.chunks = chunks
        self.fail = fail
        self.calls: list[tuple[str, str, bytes]] = []

    async def stream_response(self, allowed: Allowed, method, headers, body):
        self.calls.append((method, allowed.path, body))
        if self.fail:
            yield ("error", Denied(framing.ERR_LOCAL_UNREACHABLE, "nope"))
            return
        yield ("head", (self.status, {"content-type": "application/json"}))
        for chunk in self.chunks:
            yield ("chunk", chunk)
        yield ("end", None)


class FakeSdk:
    """The slice of PluginSDK ``ClientLink`` actually touches."""

    def __init__(self, settings, device_key: bytes | None):
        import logging

        self.log = logging.getLogger("test.ltl_remote")
        self.config = settings
        self.state = {}
        self._device_key = device_key
        self.access_log: list[dict] = []


@pytest.fixture
def link_env(monkeypatch):
    """A ClientLink wired to fakes, plus the client half of a handshake."""
    pytest.importorskip("domovoi")
    from domovoi_plugin_ltl_remote import link as link_module
    from domovoi_plugin_ltl_remote import store

    identity = crypto.Identity(
        dh=crypto.generate_keypair(), sig=crypto.generate_keypair()
    )
    device = crypto.generate_keypair()
    settings = FakeSettings()
    sdk = FakeSdk(settings, device.public_raw)
    sdk.state["identity"] = identity

    async def fake_approved_key(_sdk, device_id):
        return _sdk._device_key

    async def fake_touch(_sdk, device_id, *, country=None):
        return None

    async def fake_log(_sdk, **kwargs):
        _sdk.access_log.append(kwargs)

    monkeypatch.setattr(store, "approved_device_key", fake_approved_key)
    monkeypatch.setattr(store, "touch_device", fake_touch)
    monkeypatch.setattr(store, "log_access", fake_log)

    sent: list[tuple[int, bytes, bytes]] = []

    async def send_raw(opcode, link_id, payload):
        sent.append((opcode, link_id, payload))

    proxy = FakeProxy()
    client_link = link_module.ClientLink(
        link_id=b"\x01" * 16,
        device_id="d_test",
        country="US",
        sdk=sdk,
        settings=settings,
        proxy=proxy,
        send_raw=send_raw,
        limiter=StreamLimiter(4),
    )
    return {
        "link": client_link, "sent": sent, "proxy": proxy, "sdk": sdk,
        "identity": identity, "device": device,
    }


async def _do_handshake(env):
    """Run the client side against the ClientLink under test."""
    client = crypto.ClientHandshake(
        env["device"], env["identity"].dh.public_raw, "d_test"
    )
    await env["link"].handle_payload(framing.encode_json_payload(client.hello()))
    home_hello = framing.decode_json_payload(env["sent"][-1][2])
    confirm, sealed = client.finish(home_hello)
    await env["link"].handle_payload(framing.encode_json_payload(confirm))
    return sealed


@needs_domovoi
def test_handshake_over_the_link_establishes_a_session(link_env):
    sealed = asyncio.run(_do_handshake(link_env))
    assert sealed is not None
    assert not link_env["link"].closed


@needs_domovoi
def test_a_request_is_forwarded_and_answered_in_frames(link_env):
    async def scenario():
        sealed = await _do_handshake(link_env)
        link_env["sent"].clear()
        await link_env["link"].handle_payload(
            sealed.seal(framing.request(1, "GET", "/api/satellites", {}))
        )
        await asyncio.sleep(0)          # let the stream task run
        for _ in range(10):
            await asyncio.sleep(0)
        return [framing.decode_inner(sealed.open(p)) for _, _, p in link_env["sent"]]

    frames = asyncio.run(scenario())
    types = [f.type for f in frames]
    assert framing.RES_HEAD in types
    assert framing.RES_CHUNK in types
    assert types[-1] == framing.RES_END
    head = next(f for f in frames if f.type == framing.RES_HEAD)
    assert head.header["status"] == 200
    assert link_env["proxy"].calls == [("GET", "/api/satellites", b"")]


@needs_domovoi
def test_a_disallowed_path_never_reaches_a_local_socket(link_env):
    """The allowlist decision happens before the proxy is called at all,
    which is the property that keeps this from being an open proxy."""
    async def scenario():
        sealed = await _do_handshake(link_env)
        link_env["sent"].clear()
        await link_env["link"].handle_payload(
            sealed.seal(framing.request(3, "GET", "/etc/passwd", {}))
        )
        for _ in range(10):
            await asyncio.sleep(0)
        return [framing.decode_inner(sealed.open(p)) for _, _, p in link_env["sent"]]

    frames = asyncio.run(scenario())
    assert [f.type for f in frames] == [framing.ERROR]
    assert frames[0].header["code"] == framing.ERR_PATH_NOT_ALLOWED
    assert link_env["proxy"].calls == []
    assert link_env["sdk"].access_log[-1]["outcome"] == "denied"


@needs_domovoi
def test_an_unapproved_device_is_refused_before_key_agreement(monkeypatch, link_env):
    from domovoi_plugin_ltl_remote import store

    async def no_key(_sdk, device_id):
        return None

    monkeypatch.setattr(store, "approved_device_key", no_key)

    async def scenario():
        client = crypto.ClientHandshake(
            link_env["device"], link_env["identity"].dh.public_raw, "d_test"
        )
        await link_env["link"].handle_payload(
            framing.encode_json_payload(client.hello())
        )

    asyncio.run(scenario())
    reply = framing.decode_json_payload(link_env["sent"][0][2])
    assert reply["code"] == framing.ERR_DEVICE_NOT_APPROVED
    assert link_env["link"].closed


@needs_domovoi
def test_a_garbage_frame_closes_the_link(link_env):
    async def scenario():
        await _do_handshake(link_env)
        link_env["sent"].clear()
        await link_env["link"].handle_payload(b"\x00" * 64)

    asyncio.run(scenario())
    assert link_env["link"].closed


@needs_domovoi
def test_plaintext_after_sealing_is_rejected(link_env):
    """Once a link is sealed, an unsealed payload is a protocol
    violation — accepting one would be a downgrade path."""
    async def scenario():
        await _do_handshake(link_env)
        link_env["sent"].clear()
        await link_env["link"].handle_payload(
            framing.encode_json_payload({"t": "client_hello", "v": 1})
        )

    asyncio.run(scenario())
    assert link_env["link"].closed
