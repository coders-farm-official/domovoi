"""Ask every house; trust only the one that proves the code (1.3.1).

A room device set up through the Wi-Fi portal with no server address
(``auto``) and nothing pinned used to keep the FIRST Domovoi server that
answered on the network and wait there for approval for ever. On a network
with two servers — a live house and a test server, a neighbour on a shared
building network — that is a coin toss, and losing it means a device
nobody can approve.

Now it asks every Domovoi server it can find (``discovery.locate_all``, at
most :data:`satellite.discovery.MAX_HOUSES`), parks a request with each,
and accepts an approval only when the server PROVES it knows the code the
device said out loud: the password-authenticated exchange in
:mod:`satellite.pairing_proof`. The person reads the six digits off the
device and types them into their own dashboard; only that server can then
finish the exchange. A server that merely says "approved" — a ``ready``
with no proof — is ignored and logged.

One code for every house. The device has one code (the setup portal's,
shown as the setup network closes, and spoken by the device). It is never
sent anywhere: the exchange lets a server prove it knows the code without
either side revealing anything that would let the other test codes
offline, and the transcript binds the room and the server's identity, so a
proof from one house is no use in any other house's run. Distinct codes
per house would buy nothing the exchange doesn't already give, and would
make the person pick their house's code out of a list read aloud by the
device — with names any server can make up.

Each house gets its OWN pairing token, derived from the device's
(:func:`house_token`): a token is a bearer secret, and handing the same
one to every server on the network would let any of them impersonate the
device to the household's real server later. Once approved, the device
keeps the approving house's token and forgets the rest.

When a house approves, the device withdraws its request from every other
house it asked (best effort; requests also expire on their own after
fifteen minutes without a visit).

The device's budget: it answers at most :data:`BUDGET_MAX` challenges in
any :data:`BUDGET_WINDOW_SEC`, across all servers, persisted across
restarts — each answered challenge is one online guess for a server that
does not know the code (one in a million each).

Old servers (no ``pairing_proof`` in their health answer) are never asked
by an unpinned device: an approval from one cannot be checked. A device
whose server address was TYPED in the portal, or that is pinned to a
server's key, does not use this module against an old server — it keeps
the single-server flow it always had, because the person (or the card)
already said which server to trust. See docs/SECURITY_PRIVACY.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from satellite import pairing_proof
from satellite.discovery import House

log = logging.getLogger("satellite.house_pairing")

#: How often each house is asked again while the device waits. The core
#: checks a typed code on the device's next visit, so this is how long
#: "approve" takes to land at most.
ASK_EVERY_SEC = 10.0
#: How often the device looks for houses again (new ones can appear). The
#: subnet sweep inside it costs a /24 of probes, so not every round.
REDISCOVER_EVERY_SEC = 120.0
#: The device answers at most this many challenges in this window, across
#: every server, and remembers them across restarts.
BUDGET_MAX = 5
BUDGET_WINDOW_SEC = 1800.0
#: One visit to one house: connect, hello, the answer — and the exchange
#: when a code was typed (a few modular exponentiations on each side).
VISIT_TIMEOUT_SEC = 30.0
WITHDRAW_TIMEOUT_SEC = 8.0

_TOKEN_CONTEXT = b"domovoi-pairing-token-v1\n"


def house_token(master: str, fingerprint: str) -> str:
    """The pairing token this device presents to the server with
    ``fingerprint``: HMAC-SHA256 of the fingerprint under the device's own
    token. Different for every server, and none of them reveals the
    device's token or another server's."""
    return hmac.new(master.encode("utf-8"), _TOKEN_CONTEXT + fingerprint.encode("utf-8"),
                    hashlib.sha256).hexdigest()


class ProofBudget:
    """At most :data:`BUDGET_MAX` answered challenges per
    :data:`BUDGET_WINDOW_SEC`, kept in a small JSON file so a restart (or a
    power cut someone causes) does not reset it."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time,
                 max_attempts: int = BUDGET_MAX, window_sec: float = BUDGET_WINDOW_SEC) -> None:
        self.path = path
        self._clock = clock
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self._memory: list[float] | None = None

    def _read(self) -> list[float]:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            stamps = [float(t) for t in doc.get("answered", []) if isinstance(t, (int, float))]
        except (OSError, ValueError, AttributeError, TypeError):
            stamps = list(self._memory or [])
        now = self._clock()
        # A stamp in the future (a clock that jumped back) still counts,
        # until it is a window old by the new clock.
        return [t for t in stamps if now - t < self.window_sec]

    def _write(self, stamps: list[float]) -> None:
        self._memory = stamps
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"answered": stamps}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.debug("could not save the approval budget: %s", e)

    def remaining(self) -> int:
        return max(0, self.max_attempts - len(self._read()))

    def allow(self) -> bool:
        return self.remaining() > 0

    def spend(self) -> bool:
        """Count one answered challenge. False (and nothing counted) when
        the budget is spent."""
        stamps = self._read()
        if len(stamps) >= self.max_attempts:
            return False
        stamps.append(self._clock())
        self._write(stamps)
        return True

    def retry_after(self) -> float:
        stamps = sorted(self._read())
        if len(stamps) < self.max_attempts:
            return 0.0
        return max(0.0, stamps[0] + self.window_sec - self._clock())


@dataclass
class Visit:
    """What one visit to one house came to."""
    kind: str        # parked, approved, mismatch, refused, unproven, bad_proof,
                     # unreachable, aborted, withdrawn
    detail: str = ""


Connect = Callable[[str], Any]   # url -> async context manager of a websocket


def _stream_url(house: House, room_id: str) -> str:
    return house.url.rstrip("/") + f"/v1/stream/{room_id}"


async def _recv_json(ws: Any, timeout: float) -> dict[str, Any] | None:
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        if isinstance(raw, (bytes, bytearray)):
            continue
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if isinstance(frame, dict):
            return frame


async def visit(
    house: House,
    *,
    room_id: str,
    code: str,
    hello: dict[str, Any],
    token: str,
    connect: Connect,
    budget: ProofBudget,
    may_answer: Callable[[], Awaitable[bool]] | None = None,
    timeout: float = VISIT_TIMEOUT_SEC,
) -> Visit:
    """Ask ``house`` once: park (or refresh) the request, and when a person
    typed a code there, run the exchange.

    The hello carries this house's token and the device's half of a FRESH
    exchange, and never the code. ``may_answer`` is awaited before a
    challenge is answered (the coordinator's lock: one exchange at a time,
    none once a house has approved); False aborts the visit."""
    if not house.fingerprint:
        return Visit("refused", "the server proved no identity")
    exchange = pairing_proof.DeviceExchange(code)
    frame_out = dict(hello)
    frame_out.pop("approval_code", None)
    frame_out.pop("prev_health", None)    # this device's health is not a stranger's business
    frame_out["room_id"] = room_id
    frame_out["pairing_token"] = token
    frame_out["approval_pake"] = exchange.message
    answered = False
    try:
        async with connect(_stream_url(house, room_id)) as ws:
            await ws.send(json.dumps(frame_out))
            while True:
                frame = await _recv_json(ws, timeout)
                kind = frame.get("type")
                if kind == "error":
                    reason = str(frame.get("reason") or "")
                    if reason == "awaiting_approval":
                        return Visit("parked")
                    if reason == "approval_code_mismatch":
                        return Visit("mismatch")
                    return Visit("refused", reason or str(frame.get("message") or ""))
                if kind == "approval_challenge":
                    if answered:
                        return Visit("refused", "a second challenge in one visit")
                    if may_answer is not None and not await may_answer():
                        return Visit("aborted")
                    if not budget.spend():
                        return Visit("aborted", "budget spent")
                    tag = await asyncio.to_thread(
                        exchange.answer, frame.get("pake"),
                        room_id=room_id, fingerprint=house.fingerprint,
                    )
                    if tag is None:
                        return Visit("refused", "the challenge was not a valid exchange message")
                    answered = True
                    await ws.send(json.dumps({"type": "approval_confirm", "tag": tag}))
                    continue
                if kind == "approval_proof":
                    if answered and exchange.server_proved(frame.get("tag")):
                        return Visit("approved")
                    log.warning(
                        "%s (%s) sent an approval whose proof does not check out — "
                        "ignored; it does not know this device's code",
                        house.url, house.fingerprint,
                    )
                    return Visit("bad_proof")
                if kind == "ready":
                    # Accepted without proving anything: not an approval this
                    # device can believe, whatever the server thinks.
                    log.warning(
                        "%s (%s) accepted this device without proving it knows "
                        "the code — ignored, still waiting",
                        house.url, house.fingerprint,
                    )
                    return Visit("unproven")
                # Anything else before an answer is not ours to act on.
    except asyncio.TimeoutError:
        return Visit("unreachable", "no answer in time")
    except Exception as e:  # noqa: BLE001 — a house that fails is just not this round's
        return Visit("unreachable", str(e)[:200])


async def withdraw(house: House, *, room_id: str, token: str, connect: Connect,
                   timeout: float = WITHDRAW_TIMEOUT_SEC) -> bool:
    """Ask ``house`` to drop this device's request (another house approved
    it). Authenticated by this house's token — the one the request was
    parked with. Best effort: True when the house said it is gone."""
    try:
        async with connect(_stream_url(house, room_id)) as ws:
            await ws.send(json.dumps({
                "type": "hello", "room_id": room_id, "pairing_token": token,
                "approval_withdraw": True,
            }))
            frame = await _recv_json(ws, timeout)
            return bool(frame.get("type") == "error"
                        and frame.get("reason") == "approval_withdrawn"
                        and frame.get("withdrawn"))
    except Exception as e:  # noqa: BLE001
        log.debug("withdrawing from %s failed: %s", house.url, e)
        return False


@dataclass
class Round:
    approved: House | None = None
    parked: list[House] = field(default_factory=list)
    legacy: list[House] = field(default_factory=list)
    visits: dict[str, Visit] = field(default_factory=dict)
    budget_spent: bool = False


class Coordinator:
    """The waiting device's side: who to ask, asking them, and what to do
    when one of them proves the code."""

    def __init__(
        self,
        *,
        room_id: str,
        code: str,
        master_token: str,
        hello: Callable[[], dict[str, Any]],
        discover: Callable[[], list[House]],
        connect: Connect,
        budget: ProofBudget,
        verify: Callable[[House], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        rediscover_every: float = REDISCOVER_EVERY_SEC,
    ) -> None:
        if not pairing_proof.valid_code(code):
            raise ValueError("the approval code must be six digits")
        self.room_id = room_id
        self.code = code
        self.master_token = master_token
        self._hello = hello
        self._discover = discover
        self._connect = connect
        self._verify = verify
        self._clock = clock
        self._rediscover_every = rediscover_every
        self.budget = budget
        self.houses: list[House] = []
        self.legacy: list[House] = []
        # Every house a request was parked with: who to withdraw from.
        self.asked: dict[str, House] = {}
        self._last_discovery: float | None = None
        self._lock = asyncio.Lock()
        self._done = asyncio.Event()

    def token_for(self, house: House) -> str:
        return house_token(self.master_token, house.fingerprint or "")

    async def discover_now(self) -> None:
        found = await asyncio.to_thread(self._discover)
        self._last_discovery = self._clock()
        self.houses = [h for h in found if h.proof and h.fingerprint][: _max_houses()]
        self.legacy = [h for h in found if not h.proof]
        log.info(
            "found %d Domovoi server(s) that can prove an approval%s",
            len(self.houses),
            f", and {len(self.legacy)} that cannot" if self.legacy else "",
        )

    async def _may_answer(self) -> bool:
        await self._lock.acquire()
        if self._done.is_set():
            self._lock.release()
            return False
        return True

    async def _visit_one(self, house: House) -> Visit:
        if self._verify is not None:
            ok = await asyncio.to_thread(self._verify, house)
            if not ok:
                return Visit("unreachable", "it did not prove its identity at this address")
        held = False

        async def gate() -> bool:
            nonlocal held
            held = await self._may_answer()
            return held

        try:
            result = await visit(
                house, room_id=self.room_id, code=self.code, hello=self._hello(),
                token=self.token_for(house), connect=self._connect,
                budget=self.budget, may_answer=gate,
            )
            if result.kind == "approved":
                self._done.set()
            return result
        finally:
            if held:
                self._lock.release()

    async def round(self) -> Round:
        """Look again if it is time, then ask every house once. Returns the
        approving house, if one proved the code this round."""
        now = self._clock()
        if self._last_discovery is None or now - self._last_discovery >= self._rediscover_every:
            await self.discover_now()
        out = Round(legacy=list(self.legacy))
        if not self.budget.allow():
            out.budget_spent = True
            return out
        houses = list(self.houses)
        if not houses:
            return out
        results = await asyncio.gather(*(self._visit_one(h) for h in houses))
        for house, result in zip(houses, results):
            out.visits[house.url] = result
            if result.kind in ("parked", "mismatch", "approved", "aborted", "bad_proof", "unproven"):
                self.asked[house.fingerprint or house.url] = house
            if result.kind == "parked" or result.kind == "mismatch":
                out.parked.append(house)
            if result.kind == "approved" and out.approved is None:
                out.approved = house
        return out

    async def withdraw_others(self, approved: House) -> int:
        """Withdraw from every house asked except ``approved``. How many
        said the request is gone."""
        others = [h for key, h in self.asked.items() if key != approved.fingerprint]
        results = await asyncio.gather(*(
            withdraw(h, room_id=self.room_id, token=self.token_for(h), connect=self._connect)
            for h in others
        ))
        return sum(1 for r in results if r)


def _max_houses() -> int:
    from satellite import discovery

    return discovery.MAX_HOUSES
