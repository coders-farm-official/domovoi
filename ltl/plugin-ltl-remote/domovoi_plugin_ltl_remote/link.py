"""The outbound relay link and the per-client session state machine.

One :class:`RelayLink` holds a single WebSocket to the LTL relay for as
long as the plugin is enabled. Many :class:`ClientLink` objects multiplex
over it, one per remote device currently connected, each with its own
handshake and its own sealed stream.

The shape to keep in mind while reading:

    RelayLink            one socket, dials out, reconnects with backoff
      └── ClientLink     one remote device; owns a SealedLink after §3
            └── stream   one HTTP request or one tunneled WebSocket

Nothing here parses a request body or inspects a response. The plugin's
job is to authenticate the peer, check the path against the allowlist,
and move bytes; everything above that belongs to Domovoi's own services.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from domovoi.sdk import LongRunWorker

from . import crypto, framing, pairing, store
from .ltl_api import LtlApiClient, LtlApiError
from .crypto import CryptoError
from .framing import FramingError
from .local_proxy import (
    Allowed,
    Denied,
    LocalProxy,
    StreamLimiter,
    resolve_route,
)

# Relay control-frame verbs (PROTOCOL.md §6).
C_CHALLENGE = "challenge"
C_HELLO = "hello"
C_HELLO_OK = "hello_ok"
C_HEARTBEAT = "heartbeat"
C_QUOTA = "quota"
C_REVOKE = "revoke"
C_DEVICE_PENDING = "device_pending"
C_DEVICE_STATE = "device_state"

AGENT_VERSION = "1.0.0"

# How long a half-finished handshake may sit before it is abandoned. A
# link that opens and then says nothing is either a probe or a broken
# client; either way it should not hold a slot.
HANDSHAKE_TIMEOUT_SEC = 20.0


class ClientLink:
    """One remote device's session.

    Lifecycle: created unsealed on ``LINK_OPEN``; the first two payloads
    are the handshake; everything after is sealed. A protocol violation
    at any point closes the link rather than trying to resynchronize —
    there is no state in which guessing is better than disconnecting.
    """

    def __init__(
        self,
        *,
        link_id: bytes,
        device_id: str,
        country: str | None,
        sdk: Any,
        settings: Any,
        proxy: LocalProxy,
        send_raw: Callable[[int, bytes, bytes], Awaitable[None]],
        limiter: StreamLimiter,
    ) -> None:
        self.link_id = link_id
        self.device_id = device_id
        self.country = country
        self._sdk = sdk
        self._log = sdk.log
        self._settings = settings
        self._proxy = proxy
        self._send_raw = send_raw
        self._limiter = limiter

        self._handshake: crypto.HomeHandshake | None = None
        self._sealed: crypto.SealedLink | None = None
        self._send_lock = asyncio.Lock()
        self._opened_at = time.monotonic()

        # stream_id → queue of inbound body/socket frames
        self._inbound: dict[int, asyncio.Queue] = {}
        self._tasks: set[asyncio.Task] = set()
        self.closed = False

    # ── sending ────────────────────────────────────────────────────────

    async def _send_plain(self, obj: dict[str, Any]) -> None:
        """Only valid before the link is sealed (handshake) — after that,
        a plaintext payload is a protocol violation in either direction."""
        await self._send_raw(
            framing.OP_LINK_DATA, self.link_id, framing.encode_json_payload(obj)
        )

    async def send_inner(self, inner: bytes) -> None:
        """Seal one inner frame and put it on the wire.

        The lock matters: AES-GCM counters must be consumed in the same
        order the frames are sent, and several stream tasks write to this
        link concurrently. Sealing and sending as one atomic step is what
        keeps the receiver's strictly-increasing check from tripping on
        our own traffic.
        """
        if self._sealed is None or self.closed:
            return
        async with self._send_lock:
            frame = self._sealed.seal(inner)
            await self._send_raw(framing.OP_LINK_DATA, self.link_id, frame)

    # ── receiving ──────────────────────────────────────────────────────

    async def handle_payload(self, payload: bytes) -> None:
        if self.closed:
            return
        if self._sealed is None:
            await self._handle_handshake(payload)
            return
        try:
            inner = framing.decode_inner(self._sealed.open(payload))
        except (CryptoError, FramingError) as e:
            self._log.warning(
                "ltl_remote: link %s from %s failed frame checks: %s",
                self.link_id.hex()[:8], self.device_id, e,
            )
            await self.close(framing.ERR_PROTOCOL, "frame rejected")
            return
        await self._dispatch(inner)

    async def _handle_handshake(self, payload: bytes) -> None:
        if time.monotonic() - self._opened_at > HANDSHAKE_TIMEOUT_SEC:
            await self.close(framing.ERR_PROTOCOL, "handshake timed out")
            return
        try:
            message = framing.decode_json_payload(payload)
        except FramingError:
            await self.close(framing.ERR_PROTOCOL, "handshake is not JSON")
            return

        try:
            if self._handshake is None:
                await self._handshake_step_one(message)
            else:
                self._sealed = self._handshake.finish(message)
                self._log.info(
                    "ltl_remote: device %s established a session", self.device_id
                )
                await store.touch_device(self._sdk, self.device_id, country=self.country)
        except CryptoError as e:
            # Deliberately terse to the peer. Which check failed is
            # useful to us and useful to an attacker; it goes in our log
            # and not on the wire.
            self._log.warning(
                "ltl_remote: handshake with %s rejected: %s", self.device_id, e
            )
            await self.close(framing.ERR_PROTOCOL, "handshake rejected")

    async def _handshake_step_one(self, message: dict[str, Any]) -> None:
        approved = await store.approved_device_key(self._sdk, self.device_id)
        if approved is None:
            # The one refusal we explain properly: the user needs to know
            # to go and approve the device on their dashboard, and this
            # tells an attacker nothing they could not learn by trying.
            self._log.info(
                "ltl_remote: refused unapproved device %s", self.device_id
            )
            await self._send_plain({
                "t": "error",
                "code": framing.ERR_DEVICE_NOT_APPROVED,
                "message": "approve this device on the Domovoi dashboard first",
            })
            await self.close(framing.ERR_DEVICE_NOT_APPROVED, "not approved")
            return
        identity: crypto.Identity = self._sdk.state["identity"]
        self._handshake = crypto.HomeHandshake(identity.dh)
        response = self._handshake.respond(message, approved)
        response["household_fp"] = identity.fingerprint
        await self._send_plain(response)

    # ── inner-frame dispatch ───────────────────────────────────────────

    async def _dispatch(self, inner: framing.InnerFrame) -> None:
        if inner.type == framing.REQ:
            await self._start_stream(self._run_request(inner))
        elif inner.type == framing.WS_OPEN:
            await self._start_stream(self._run_websocket(inner))
        elif inner.type in (
            framing.REQ_CHUNK, framing.REQ_END, framing.WS_DATA, framing.WS_CLOSE
        ):
            queue = self._inbound.get(inner.stream_id)
            if queue is not None:
                queue.put_nowait(inner)
            # A frame for an unknown stream is dropped, not an error: the
            # stream may have just finished, and a race is not a fault.
        elif inner.type == framing.PING:
            await self.send_inner(framing.pong(int(inner.header.get("ts", 0))))
        elif inner.type == framing.PONG:
            pass
        else:
            await self.send_inner(
                framing.error(inner.stream_id, framing.ERR_PROTOCOL, "unexpected frame")
            )

    async def _start_stream(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── HTTP requests ──────────────────────────────────────────────────

    async def _run_request(self, inner: framing.InnerFrame) -> None:
        stream_id = inner.stream_id
        method = str(inner.header.get("method", ""))
        path = str(inner.header.get("path", ""))
        started = time.monotonic()

        decision = resolve_route(method, path, self._settings)
        if isinstance(decision, Denied):
            await self.send_inner(
                framing.error(stream_id, decision.code, decision.message)
            )
            await store.log_access(
                self._sdk, device_id=self.device_id, method=method or "?",
                path=path or "?", status=None, outcome="denied",
                denial_code=decision.code,
            )
            return

        if not self._limiter.has_room:
            await self.send_inner(
                framing.error(
                    stream_id, framing.ERR_TOO_MANY_STREAMS,
                    "too many requests in flight; try again shortly",
                )
            )
            return

        with self._limiter:
            body = inner.body
            if inner.header.get("streaming"):
                collected = await self._collect_body(stream_id)
                if collected is None:
                    await self.send_inner(
                        framing.error(
                            stream_id, framing.ERR_BODY_TOO_LARGE,
                            "request body exceeds this server's limit",
                        )
                    )
                    return
                body = collected

            headers = dict(inner.header.get("headers") or {})
            status: int | None = None
            bytes_out = 0
            outcome = "ok"
            try:
                async for kind, value in self._proxy.stream_response(
                    decision, method, headers, body
                ):
                    if kind == "head":
                        status, response_headers = value
                        await self.send_inner(
                            framing.response_head(stream_id, status, response_headers)
                        )
                    elif kind == "chunk":
                        bytes_out += len(value)
                        await self.send_inner(framing.response_chunk(stream_id, value))
                    elif kind == "error":
                        outcome = "error"
                        await self.send_inner(
                            framing.error(stream_id, value.code, value.message)
                        )
                        break
                    else:
                        await self.send_inner(framing.response_end(stream_id))
            finally:
                self._inbound.pop(stream_id, None)
                await store.log_access(
                    self._sdk, device_id=self.device_id, method=method, path=path,
                    status=status, outcome=outcome, bytes_in=len(body),
                    bytes_out=bytes_out,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

    async def _collect_body(self, stream_id: int) -> bytes | None:
        """Accumulate ``REQ_CHUNK`` frames until ``REQ_END``.

        Returns ``None`` if the body exceeds the configured cap, which is
        checked while accumulating rather than after — the point of a cap
        is to never hold the oversized thing in the first place.
        """
        limit = int(getattr(self._settings, "max_request_body_mb", 32)) * 1024 * 1024
        queue: asyncio.Queue = asyncio.Queue()
        self._inbound[stream_id] = queue
        parts: list[bytes] = []
        total = 0
        timeout = float(getattr(self._settings, "stream_idle_timeout_sec", 300.0))
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            if frame.type == framing.REQ_END:
                return b"".join(parts)
            total += len(frame.body)
            if total > limit:
                return None
            parts.append(frame.body)

    # ── tunneled WebSockets ────────────────────────────────────────────

    async def _run_websocket(self, inner: framing.InnerFrame) -> None:
        """Bridge a remote WebSocket to a local one.

        This is the path the dashboard's live state uses today and the
        one core's ``/v1/stream/{room_id}`` will use when phone voice
        lands. The plugin stays a pipe in both cases: it copies frames
        and never interprets them.
        """
        stream_id = inner.stream_id
        path = str(inner.header.get("path", ""))
        decision = resolve_route("GET", path, self._settings)
        if isinstance(decision, Denied):
            await self.send_inner(
                framing.error(stream_id, decision.code, decision.message)
            )
            await store.log_access(
                self._sdk, device_id=self.device_id, method="WS", path=path or "?",
                status=None, outcome="denied", denial_code=decision.code,
            )
            return
        if not decision.route.websocket:
            await self.send_inner(
                framing.error(
                    stream_id, framing.ERR_PATH_NOT_ALLOWED,
                    "this path is not a WebSocket endpoint",
                )
            )
            return

        queue: asyncio.Queue = asyncio.Queue()
        self._inbound[stream_id] = queue
        headers = dict(inner.header.get("headers") or {})
        if not self._limiter.has_room:
            await self.send_inner(
                framing.error(
                    stream_id, framing.ERR_TOO_MANY_STREAMS,
                    "too many sockets in flight; try again shortly",
                )
            )
            return
        try:
            with self._limiter:
                async with self._proxy.connect_websocket(decision, headers) as local:
                    await self.send_inner(framing.ws_open_ok(stream_id))
                    await self._pump_websocket(stream_id, local, queue)
        except Exception as e:  # noqa: BLE001 — every local failure reads alike
            self._sdk.log.info("ltl_remote: websocket %s ended: %s", path, e)
            await self.send_inner(
                framing.error(
                    stream_id, framing.ERR_LOCAL_UNREACHABLE,
                    "the Domovoi service closed the connection",
                )
            )
        finally:
            self._inbound.pop(stream_id, None)
            await self.send_inner(framing.ws_close(stream_id))

    async def _pump_websocket(
        self, stream_id: int, local: Any, queue: asyncio.Queue
    ) -> None:
        async def remote_to_local() -> None:
            while True:
                frame = await queue.get()
                if frame.type == framing.WS_CLOSE:
                    return
                if frame.type == framing.WS_DATA:
                    binary = bool(frame.header.get("binary"))
                    await local.send(frame.body if binary else frame.body.decode("utf-8"))

        async def local_to_remote() -> None:
            async for message in local:
                if isinstance(message, str):
                    await self.send_inner(
                        framing.ws_data(stream_id, message.encode("utf-8"), binary=False)
                    )
                else:
                    await self.send_inner(
                        framing.ws_data(stream_id, message, binary=True)
                    )

        # Whichever direction ends first ends the bridge; the other is
        # cancelled rather than left pumping into a dead socket.
        tasks = [
            asyncio.ensure_future(remote_to_local()),
            asyncio.ensure_future(local_to_remote()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── teardown ───────────────────────────────────────────────────────

    async def close(self, code: str = "", reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        for task in list(self._tasks):
            task.cancel()
        self._inbound.clear()
        with contextlib.suppress(Exception):
            await self._send_raw(
                framing.OP_LINK_CLOSE,
                self.link_id,
                framing.encode_json_payload({"code": code, "reason": reason}),
            )


class RelayLink(LongRunWorker):
    """Holds the outbound WebSocket to the LTL relay.

    A ``LongRunWorker`` rather than a poll worker because the connection
    is the point: reconnection is handled in-loop (the worker runner's
    restart-with-backoff is only the safety net for a crash we did not
    anticipate).

    ``stub_suppressed`` keeps it out of the test suite entirely — an
    outbound network connection has no place in ``USE_STUBS=true``.
    """

    name = "ltl_relay_link"
    enabled_setting = "enabled"
    stub_suppressed = True

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._log = sdk.log
        self._proxy = LocalProxy(sdk.config, log=sdk.log)
        self._api = LtlApiClient(sdk)
        self._links: dict[bytes, ClientLink] = {}
        self._socket: Any = None
        self._send_lock = asyncio.Lock()
        self._limiter: StreamLimiter | None = None

    # ── the run loop ───────────────────────────────────────────────────

    async def run(self, shutdown: asyncio.Event) -> None:
        settings = self._sdk.config
        backoff = float(settings.reconnect_initial_backoff_sec)
        while not shutdown.is_set():
            if not (settings.relay_token and settings.household_id):
                # Not paired yet. If an enrollment is open, poll it so
                # pairing completes on its own — an admin who typed the
                # code into the LTL site should not also have to sit on
                # the dashboard page. Otherwise idle cheaply rather than
                # hammering a relay that would refuse us anyway.
                claimed = await self._poll_enrollment()
                await self._sleep_or_stop(shutdown, 5.0 if not claimed else 0.1)
                continue
            try:
                await self._session(shutdown)
                backoff = float(settings.reconnect_initial_backoff_sec)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — any failure means: retry
                self._log.warning("ltl_remote: relay session ended: %s", e)
                await store.set_connection_state(
                    self._sdk, "disconnected", error=str(e)[:500]
                )
            finally:
                await self._drop_all_links()
            if shutdown.is_set():
                break
            # Full jitter. Without it, a relay restart brings every
            # household back in the same second and the reconnect storm
            # is worse than the outage.
            delay = random.uniform(0, backoff)
            self._log.info("ltl_remote: reconnecting in %.1fs", delay)
            await self._sleep_or_stop(shutdown, delay)
            backoff = min(backoff * 2, float(settings.reconnect_max_backoff_sec))

    async def _poll_enrollment(self) -> bool:
        """Check an in-flight pairing. Returns True once it is claimed.

        Failures here are logged at debug and swallowed: LTL being
        briefly unreachable during pairing is ordinary, and it must not
        put an error banner on the dashboard or burn the pairing window.
        """
        state = await store.get_link_state(self._sdk)
        enrollment_id = state.get("enrollment_id")
        token = state.get("enrollment_token")
        if not enrollment_id or not token:
            return False
        expires_at = state.get("pairing_expires_at")
        if expires_at is not None and expires_at < datetime.now(timezone.utc):
            self._log.info("ltl_remote: pairing code expired before it was claimed")
            await store.clear_pairing(self._sdk)
            return False
        try:
            result = await self._api.poll_enrollment(str(enrollment_id), str(token))
        except LtlApiError as e:
            self._log.debug("ltl_remote: enrollment poll failed: %s", e)
            return False
        if result.get("status") != "claimed":
            return False

        from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

        PLUGIN_CONFIG.write_values(
            self._sdk.slug,
            {
                "household_id": result["household_id"],
                "relay_token": result["relay_token"],
            },
        )
        await store.record_claim(
            self._sdk,
            household_id=result["household_id"],
            account_label=result.get("account_label") or None,
        )
        self._log.info(
            "ltl_remote: paired with LTL household %s", result["household_id"]
        )
        return True

    @staticmethod
    async def _sleep_or_stop(shutdown: asyncio.Event, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=max(seconds, 0.1))

    async def _session(self, shutdown: asyncio.Event) -> None:
        import websockets

        settings = self._sdk.config
        self._limiter = StreamLimiter(int(settings.max_concurrent_streams))
        await store.set_connection_state(self._sdk, "connecting")

        async with websockets.connect(
            settings.relay_url,
            additional_headers={
                "Authorization": f"Bearer {settings.relay_token}",
                "X-Household-Id": settings.household_id,
            },
            open_timeout=15,
            ping_interval=float(settings.heartbeat_sec),
            ping_timeout=float(settings.heartbeat_sec) * 2,
            max_size=framing.MAX_OUTER_BYTES,
        ) as socket:
            self._socket = socket
            await self._authenticate(socket)
            await store.set_connection_state(self._sdk, "connected")
            self._log.info("ltl_remote: relay link established")
            try:
                await self._receive_loop(socket, shutdown)
            finally:
                self._socket = None
                await store.set_connection_state(self._sdk, "disconnected")

    async def _authenticate(self, socket: Any) -> None:
        """PROTOCOL.md §8: prove possession of the household signing key
        over a relay-chosen challenge. The bearer token got us this far;
        the signature is what makes a stolen token insufficient."""
        raw = await asyncio.wait_for(socket.recv(), timeout=15)
        frame = framing.decode_outer(raw if isinstance(raw, bytes) else raw.encode())
        message = framing.decode_json_payload(frame.payload)
        if frame.opcode != framing.OP_CONTROL or message.get("t") != C_CHALLENGE:
            raise RuntimeError("relay did not open with a challenge")

        identity: crypto.Identity = self._sdk.state["identity"]
        settings = self._sdk.config
        await self._send_raw(
            framing.OP_CONTROL,
            framing.ZERO_LINK_ID,
            framing.encode_json_payload({
                "t": C_HELLO,
                "household_id": settings.household_id,
                "agent_version": AGENT_VERSION,
                "plugin_version": self._sdk.version,
                "fingerprint": identity.fingerprint,
                "sig": crypto.sign_challenge(
                    identity.sig,
                    crypto.unb64u(message.get("nonce", "")),
                    settings.household_id,
                ),
            }),
        )
        raw = await asyncio.wait_for(socket.recv(), timeout=15)
        frame = framing.decode_outer(raw if isinstance(raw, bytes) else raw.encode())
        reply = framing.decode_json_payload(frame.payload)
        if reply.get("t") != C_HELLO_OK:
            raise RuntimeError(f"relay refused the agent: {reply.get('reason', reply)}")
        await self._apply_quota(reply)

    async def _receive_loop(self, socket: Any, shutdown: asyncio.Event) -> None:
        stop = asyncio.ensure_future(shutdown.wait())
        try:
            while not shutdown.is_set():
                receive = asyncio.ensure_future(socket.recv())
                done, _ = await asyncio.wait(
                    {receive, stop}, return_when=asyncio.FIRST_COMPLETED
                )
                if stop in done:
                    receive.cancel()
                    return
                raw = receive.result()
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                try:
                    await self._handle_outer(framing.decode_outer(raw))
                except FramingError as e:
                    # A relay that cannot frame is not one we can talk to.
                    self._log.warning("ltl_remote: bad frame from relay: %s", e)
                    return
        finally:
            stop.cancel()

    async def _handle_outer(self, frame: framing.OuterFrame) -> None:
        if frame.opcode == framing.OP_CONTROL:
            await self._handle_control(framing.decode_json_payload(frame.payload))
            return
        if frame.opcode == framing.OP_LINK_OPEN:
            await self._open_link(frame)
            return
        link = self._links.get(frame.link_id)
        if link is None:
            return
        if frame.opcode == framing.OP_LINK_CLOSE:
            await link.close()
            self._links.pop(frame.link_id, None)
        else:
            await link.handle_payload(frame.payload)

    async def _open_link(self, frame: framing.OuterFrame) -> None:
        info = framing.decode_json_payload(frame.payload)
        device_id = str(info.get("device_id", ""))
        if not device_id:
            return
        assert self._limiter is not None
        link = ClientLink(
            link_id=frame.link_id,
            device_id=device_id,
            country=info.get("ip_country"),
            sdk=self._sdk,
            settings=self._sdk.config,
            proxy=self._proxy,
            send_raw=self._send_raw,
            limiter=self._limiter,
        )
        self._links[frame.link_id] = link

    async def _handle_control(self, message: dict[str, Any]) -> None:
        verb = message.get("t")
        if verb == C_QUOTA:
            await self._apply_quota(message)
        elif verb == C_DEVICE_PENDING:
            await self._register_pending(message)
        elif verb == C_REVOKE:
            self._log.warning(
                "ltl_remote: relay revoked the link: %s",
                message.get("reason", "no reason given"),
            )
            await store.set_connection_state(
                self._sdk, "revoked", error=str(message.get("reason", ""))[:500]
            )
            raise RuntimeError("relay revoked this household's link")
        elif verb == C_HEARTBEAT:
            await self._send_raw(
                framing.OP_CONTROL,
                framing.ZERO_LINK_ID,
                framing.encode_json_payload({"t": C_HEARTBEAT}),
            )

    async def _apply_quota(self, message: dict[str, Any]) -> None:
        if "used_bytes" not in message and "plan" not in message:
            return
        period_end = message.get("period_end")
        await store.record_quota(
            self._sdk,
            plan_code=message.get("plan"),
            used_bytes=int(message.get("used_bytes") or 0),
            limit_bytes=message.get("limit_bytes"),
            period_end=_parse_timestamp(period_end),
        )

    async def _register_pending(self, message: dict[str, Any]) -> None:
        """A device registered at LTL and wants in.

        Recording it as ``pending`` is the whole of LTL's influence over
        access: the device cannot reach anything until a human approves
        it on this dashboard.
        """
        try:
            public_key = crypto.unb64u(str(message.get("public_key", "")))
            crypto.load_public(public_key)          # reject junk at the door
        except CryptoError as e:
            self._log.warning("ltl_remote: relay sent an unusable device key: %s", e)
            return
        device_id = str(message.get("device_id", ""))
        if not device_id:
            return
        added = await store.register_pending_device(
            self._sdk,
            device_id=device_id,
            label=str(message.get("label") or "Unnamed device")[:120],
            public_key=public_key,
            fingerprint=pairing.device_fingerprint(public_key),
        )
        if added:
            self._log.info(
                "ltl_remote: device %s is waiting for approval", device_id
            )

    # ── plumbing ───────────────────────────────────────────────────────

    async def _send_raw(self, opcode: int, link_id: bytes, payload: bytes) -> None:
        socket = self._socket
        if socket is None:
            return
        async with self._send_lock:
            await socket.send(framing.encode_outer(opcode, link_id, payload))

    async def report_device_state(self, device_id: str, status: str) -> None:
        """Tell LTL what this household decided about a device.

        A report, not a request for permission — the approval already
        happened, locally, and this only keeps the LTL web app from
        showing a stale state next to the device. Best-effort by design:
        if the link is down, the decision still stands here, which is the
        only place it has ever mattered.
        """
        await self._send_raw(
            framing.OP_CONTROL,
            framing.ZERO_LINK_ID,
            framing.encode_json_payload({
                "t": C_DEVICE_STATE,
                "device_id": device_id,
                "status": status,
            }),
        )
        if status == "revoked":
            # Drop any live link the device is holding, so revoking takes
            # effect now rather than whenever it next reconnects.
            for link in [l for l in self._links.values() if l.device_id == device_id]:
                with contextlib.suppress(Exception):
                    await link.close(framing.ERR_DEVICE_NOT_APPROVED, "revoked")
                self._links.pop(link.link_id, None)

    async def _drop_all_links(self) -> None:
        for link in list(self._links.values()):
            with contextlib.suppress(Exception):
                await link.close()
        self._links.clear()

    async def shutdown_links(self) -> None:
        """Called from the plugin's ``on_disable`` teardown so a disable
        closes cleanly instead of waiting for a socket timeout."""
        await self._drop_all_links()
        socket = self._socket
        if socket is not None:
            with contextlib.suppress(Exception):
                await socket.close()


def _parse_timestamp(value: Any):
    """Relay timestamps are ISO-8601 strings. A malformed one is dropped
    rather than raised on: a bad quota timestamp must not take the link
    down."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = ["ClientLink", "RelayLink", "StreamLimiter"]
