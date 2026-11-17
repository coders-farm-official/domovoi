"""Ask every house; trust only the one that proves the code (1.4.0).

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

The device's budget (:class:`ProofBudget`), persisted across restarts and
counted in RUNNING time (the monotonic clock: a clock that jumps, or a
server that sets it, cannot reset it):

* at most :data:`BUDGET_MAX` answered challenges in any
  :data:`BUDGET_WINDOW_SEC`, and :data:`BUDGET_DAILY_MAX` in any
  :data:`BUDGET_DAY_SEC`, across every server;
* a server whose run did not end in a valid proof (keyed by its
  fingerprint AND its address) is not answered again for a growing while
  (:data:`HOLD_BASE_SEC`, doubling, at most :data:`HOLD_MAX_SEC`);
* while it may not answer, the device keeps visiting — so its requests do
  not expire — and DECLINES each challenge (``approval_decline``): the core
  puts the typed code back, and nothing is counted against anyone.

Each answered challenge is one online guess for a server that does not
know the code (one in a million each).

One visit is bounded as a whole (:data:`VISIT_TIMEOUT_SEC`), and after the
device has sent its confirmation it waits :data:`PROOF_WAIT_SEC` for the
proof and accepts nothing else: a server that keeps a socket alive with
other frames cannot hold the device, or the one-exchange-at-a-time lock,
for longer than that. A round returns the moment one house proves the code.

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
#: The device answers at most this many challenges in this much RUNNING
#: time, across every server, and remembers them across restarts.
BUDGET_MAX = 5
BUDGET_WINDOW_SEC = 1800.0
#: And at most this many in a day of running time.
BUDGET_DAILY_MAX = 20
BUDGET_DAY_SEC = 86400.0
#: A server (its fingerprint, and separately its address) whose run did not
#: end in a valid proof is not answered for this long, doubling with each
#: such run, up to HOLD_MAX_SEC. Forgotten after a day without one.
HOLD_BASE_SEC = 30.0
HOLD_MAX_SEC = 300.0
#: One visit to one house, ALL of it: connect, hello, the answer — and the
#: exchange when a code was typed (a few modular exponentiations on each
#: side). Not per frame: a server that keeps talking does not keep the
#: device.
VISIT_TIMEOUT_SEC = 30.0
#: After the device sent its confirmation: how long it waits for the
#: server's proof (the server checks a tag and writes one row). Nothing
#: but the proof or an error is accepted meanwhile.
PROOF_WAIT_SEC = 6.0
#: How long a challenge waits for another house's exchange to finish
#: before the device declines it. The core waits 20 s for the device's
#: confirmation after its challenge; this plus the device's own arithmetic
#: has to stay inside that.
ANSWER_LOCK_WAIT_SEC = 10.0
#: The identity check before each visit (one HTTP round trip).
VERIFY_TIMEOUT_SEC = 8.0
#: Once a house has proved the code, how long the round's other visits get
#: to finish (declining a challenge politely) before they are cut.
ROUND_GRACE_SEC = 1.0
WITHDRAW_TIMEOUT_SEC = 8.0

_TOKEN_CONTEXT = b"domovoi-pairing-token-v1\n"


def house_token(master: str, fingerprint: str) -> str:
    """The pairing token this device presents to the server with
    ``fingerprint``: HMAC-SHA256 of the fingerprint under the device's own
    token. Different for every server, and none of them reveals the
    device's token or another server's."""
    return hmac.new(master.encode("utf-8"), _TOKEN_CONTEXT + fingerprint.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _house_keys(house: House) -> list[str]:
    """What a server's share of the budget is kept under: its proved
    fingerprint AND the address it answered on — a hostile host can mint a
    new key in a millisecond, a new address takes more."""
    from satellite import discovery

    keys = []
    if house.fingerprint:
        keys.append("fp:" + house.fingerprint)
    host, port = discovery.host_port_of(house.url)
    if host:
        keys.append(f"addr:{host}:{port}")
    return keys


class ProofBudget:
    """How many challenges this device may still answer, and whom.

    Persisted in a small JSON file so a restart (or a power cut someone
    causes) does not reset it, and counted in RUNNING time: every entry
    holds how long ago it happened in seconds of the monotonic clock, aged
    only while the device runs. A wall clock that steps forward — a Pi with
    no battery clock syncing at boot, or a server it copies the time from —
    changes nothing here; time the device spends switched off does not
    count either (it waits longer, never shorter).

    A file that exists but cannot be read, or that cannot be written when
    an answer must be counted, makes the budget SPENT for this run: a
    device that cannot remember its guesses must not hand out more."""

    VERSION = 2

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic,
                 max_attempts: int = BUDGET_MAX, window_sec: float = BUDGET_WINDOW_SEC,
                 daily_max: int = BUDGET_DAILY_MAX, day_sec: float = BUDGET_DAY_SEC,
                 hold_base_sec: float = HOLD_BASE_SEC,
                 hold_max_sec: float = HOLD_MAX_SEC) -> None:
        self.path = path
        self._clock = clock
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self.daily_max = daily_max
        self.day_sec = day_sec
        self.hold_base_sec = hold_base_sec
        self.hold_max_sec = hold_max_sec
        self._loaded = False
        self._base = 0.0
        self._answered: list[float] = []                 # ages, running seconds
        self._holds: dict[str, dict[str, float]] = {}    # key -> {fails, age}
        #: Why the budget cannot be trusted this run (None: it can).
        self.broken: str | None = None
        self._saved_shape: tuple | None = None

    # ── state ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        self._loaded = True
        self._base = self._clock()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._saved_shape = self._shape()
            return
        except (OSError, UnicodeDecodeError) as e:
            self._break(f"cannot read {self.path}: {e}")
            return
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError("not a document")
            if doc.get("v") == self.VERSION:
                ages = [float(a) for a in doc.get("answered_age", [])]
                holds = {
                    str(k): {"fails": float(v["fails"]), "age": float(v["age"])}
                    for k, v in dict(doc.get("holds") or {}).items()
                }
            else:
                # The first version kept wall-clock stamps. Their age is not
                # knowable without trusting the clock, so each counts as
                # brand new: conservative, and it happens once.
                ages = [0.0 for t in doc.get("answered", []) if isinstance(t, (int, float))]
                holds = {}
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            self._break(f"{self.path} is not readable as an approval budget: {e}")
            return
        # NaN is not a number of seconds; a negative age is clamped to now.
        self._answered = [max(0.0, a) for a in ages if a == a][: 10 * max(1, self.daily_max)]
        self._holds = {k: {"fails": max(1.0, v["fails"]), "age": max(0.0, v["age"])}
                       for k, v in holds.items() if v["age"] == v["age"]}
        self._saved_shape = self._shape()

    def _break(self, why: str) -> None:
        if self.broken is None:
            log.error(
                "the approval budget cannot be trusted (%s): this device answers no "
                "more approval challenges until it restarts with a readable, "
                "writable %s", why, self.path,
            )
        self.broken = why

    def _hold_for(self, fails: float) -> float:
        return min(self.hold_max_sec, self.hold_base_sec * 2 ** max(0.0, fails - 1))

    def _tick(self) -> None:
        if not self._loaded:
            self._load()
        now = self._clock()
        elapsed = max(0.0, now - self._base)
        self._base = now
        if elapsed:
            self._answered = [a + elapsed for a in self._answered]
            for v in self._holds.values():
                v["age"] += elapsed
        self._answered = [a for a in self._answered if a < self.day_sec]
        self._holds = {k: v for k, v in self._holds.items() if v["age"] < self.day_sec}
        if self.broken is None and self._shape() != self._saved_shape:
            # Something left a window: write the ages down, so a restart
            # does not count them again from where they were last saved.
            self._save()

    def _shape(self) -> tuple:
        in_window = sum(1 for a in self._answered if a < self.window_sec)
        held = tuple(sorted(k for k, v in self._holds.items()
                            if v["age"] < self._hold_for(v["fails"])))
        return in_window, len(self._answered), held, len(self._holds)

    def _save(self) -> bool:
        doc = {
            "v": self.VERSION,
            "answered_age": [round(a, 3) for a in self._answered],
            "holds": {k: {"fails": v["fails"], "age": round(v["age"], 3)}
                      for k, v in self._holds.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            self._break(f"cannot write {self.path}: {e}")
            return False
        self._saved_shape = self._shape()
        return True

    # ── questions ──────────────────────────────────────────────────────

    def remaining(self) -> int:
        """Answers left right now, across every server."""
        self._tick()
        if self.broken is not None:
            return 0
        in_window = sum(1 for a in self._answered if a < self.window_sec)
        return max(0, min(self.max_attempts - in_window, self.daily_max - len(self._answered)))

    def allow(self) -> bool:
        return self.remaining() > 0

    def held(self, house: House) -> float:
        """Seconds until ``house`` may be answered again (0: now)."""
        self._tick()
        wait = 0.0
        for key in _house_keys(house):
            v = self._holds.get(key)
            if v is not None:
                wait = max(wait, self._hold_for(v["fails"]) - v["age"])
        return max(0.0, wait)

    def may_answer(self, house: House | None = None) -> str | None:
        """None when a challenge (from ``house``) may be answered now, else
        why not."""
        if not self.allow():
            return "budget spent" if self.broken is None else "budget cannot be trusted"
        if house is not None:
            wait = self.held(house)
            if wait > 0:
                return f"held for {int(wait) + 1} s after a run that proved nothing"
        return None

    def spend(self, house: House | None = None) -> bool:
        """Count one answered challenge — on disk FIRST. False (and nothing
        to answer with) when the budget is spent, ``house`` is held, or the
        count could not be saved."""
        if self.may_answer(house) is not None:
            return False
        self._answered.append(0.0)
        return self._save()

    def fail(self, house: House) -> None:
        """A run with ``house`` that this device answered ended without a
        valid proof: hold it, and its address, for a growing while."""
        self._tick()
        for key in _house_keys(house):
            v = self._holds.get(key)
            self._holds[key] = {"fails": (v["fails"] + 1.0) if v else 1.0, "age": 0.0}
        if self.broken is None:
            self._save()

    def retry_after(self) -> float:
        """Seconds of running time until the next answer is possible, to
        any server (``inf`` when the budget cannot be trusted)."""
        self._tick()
        if self.broken is not None:
            return float("inf")
        waits = [0.0]
        in_window = sorted((a for a in self._answered if a < self.window_sec), reverse=True)
        if len(in_window) >= self.max_attempts:
            waits.append(self.window_sec - in_window[len(in_window) - self.max_attempts])
        by_age = sorted(self._answered, reverse=True)
        if len(by_age) >= self.daily_max:
            waits.append(self.day_sec - by_age[len(by_age) - self.daily_max])
        return max(0.0, max(waits))


@dataclass
class Visit:
    """What one visit to one house came to."""
    kind: str        # parked, approved, mismatch, refused, unproven, bad_proof,
                     # declined, unreachable, withdrawn
    detail: str = ""


Connect = Callable[[str], Any]   # url -> async context manager of a websocket


def _stream_url(house: House, room_id: str) -> str:
    return house.url.rstrip("/") + f"/v1/stream/{room_id}"


async def _recv_json(ws: Any) -> dict[str, Any]:
    """The next JSON-object text frame. No timeout of its own: every caller
    bounds it (the visit as a whole, or the wait for the proof)."""
    while True:
        raw = await ws.recv()
        if isinstance(raw, (bytes, bytearray)):
            continue
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if isinstance(frame, dict):
            return frame


async def _decline(ws: Any, reason: str) -> None:
    """Tell the server this device will not answer its challenge now, so it
    keeps the typed code for a later visit and counts nothing."""
    try:
        await ws.send(json.dumps({"type": "approval_decline", "reason": reason}))
    except Exception as e:  # noqa: BLE001 — it times out on its own otherwise
        log.debug("could not decline the challenge: %s", e)


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
    timeout: float | None = None,
    proof_wait: float | None = None,
) -> Visit:
    """Ask ``house`` once: park (or refresh) the request, and when a person
    typed a code there, run the exchange.

    The hello carries this house's token and the device's half of a FRESH
    exchange, and never the code. ``may_answer`` is awaited before a
    challenge is answered (the coordinator's lock: one exchange at a time,
    none once a house has approved); False declines it. So does the budget
    (:meth:`ProofBudget.may_answer`). ``timeout`` bounds the whole visit;
    a run this device answered that does not end in a valid proof holds
    the house (:meth:`ProofBudget.fail`)."""
    if not house.fingerprint:
        return Visit("refused", "the server proved no identity")
    timeout = VISIT_TIMEOUT_SEC if timeout is None else timeout
    proof_wait = PROOF_WAIT_SEC if proof_wait is None else proof_wait
    state = {"answered": False}
    try:
        result = await asyncio.wait_for(
            _visit(house, room_id=room_id, code=code, hello=hello, token=token,
                   connect=connect, budget=budget, may_answer=may_answer,
                   proof_wait=proof_wait, state=state),
            timeout,
        )
    except asyncio.TimeoutError:
        result = Visit("unreachable", "no answer in time")
    except asyncio.CancelledError:
        if state["answered"]:
            budget.fail(house)
        raise
    except Exception as e:  # noqa: BLE001 — a house that fails is just not this round's
        result = Visit("unreachable", str(e)[:200])
    if state["answered"] and result.kind != "approved":
        budget.fail(house)
    return result


async def _visit(
    house: House, *, room_id: str, code: str, hello: dict[str, Any], token: str,
    connect: Connect, budget: ProofBudget,
    may_answer: Callable[[], Awaitable[bool]] | None, proof_wait: float,
    state: dict[str, bool],
) -> Visit:
    assert house.fingerprint
    # Two exponentiations: off the loop (about a second on a Pi Zero 2 W).
    exchange = await asyncio.to_thread(pairing_proof.DeviceExchange, code)
    frame_out = dict(hello)
    frame_out.pop("approval_code", None)
    frame_out.pop("prev_health", None)    # this device's health is not a stranger's business
    frame_out["room_id"] = room_id
    frame_out["pairing_token"] = token
    frame_out["approval_pake"] = exchange.message
    async with connect(_stream_url(house, room_id)) as ws:
        await ws.send(json.dumps(frame_out))
        while True:
            frame = await _recv_json(ws)
            kind = frame.get("type")
            if kind == "error":
                reason = str(frame.get("reason") or "")
                if reason == "awaiting_approval":
                    return Visit("parked")
                if reason == "approval_code_mismatch":
                    return Visit("mismatch")
                return Visit("refused", reason or str(frame.get("message") or ""))
            if kind == "approval_challenge":
                if may_answer is not None and not await may_answer():
                    await _decline(ws, "busy")
                    return Visit("declined", "another exchange is running, or a house approved")
                why = budget.may_answer(house)
                if why is None and not budget.spend(house):
                    why = budget.may_answer(house) or "the budget could not be saved"
                if why is not None:
                    log.info("%s asked this device to check a typed code; declined for "
                             "now (%s) — the code waits there", house.url, why)
                    await _decline(ws, "budget")
                    return Visit("declined", why)
                state["answered"] = True
                tag = await asyncio.to_thread(
                    exchange.answer, frame.get("pake"),
                    room_id=room_id, fingerprint=house.fingerprint,
                )
                if tag is None:
                    return Visit("refused", "the challenge was not a valid exchange message")
                await ws.send(json.dumps({"type": "approval_confirm", "tag": tag}))
                return await _await_proof(ws, house, exchange, proof_wait)
            if kind == "approval_proof":
                # A proof before this device answered anything.
                log.warning("%s (%s) sent an approval proof nobody asked for — ignored",
                            house.url, house.fingerprint)
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
            # Anything else before a challenge is not ours to act on; the
            # visit's own deadline bounds how long it may go on.


async def _await_proof(ws: Any, house: House, exchange: Any, proof_wait: float) -> Visit:
    """After the confirmation: the server's proof, its refusal, or nothing
    — and only for ``proof_wait`` seconds."""
    try:
        frame = await asyncio.wait_for(_recv_json(ws), proof_wait)
    except asyncio.TimeoutError:
        log.warning("%s (%s) did not send its proof in time after this device "
                    "answered — not approved", house.url, house.fingerprint)
        return Visit("unproven", "no proof in time")
    kind = frame.get("type")
    if kind == "approval_proof":
        if exchange.server_proved(frame.get("tag")):
            return Visit("approved")
        log.warning(
            "%s (%s) sent an approval whose proof does not check out — "
            "ignored; it does not know this device's code",
            house.url, house.fingerprint,
        )
        return Visit("bad_proof")
    if kind == "error" and frame.get("reason") == "approval_code_mismatch":
        return Visit("mismatch")
    if kind == "error":
        return Visit("refused", str(frame.get("reason") or ""))
    log.warning("%s (%s) answered %r where its proof belonged — not approved",
                house.url, house.fingerprint, kind)
    return Visit("unproven", f"{kind!r} instead of a proof")


async def withdraw(house: House, *, room_id: str, token: str, connect: Connect,
                   timeout: float = WITHDRAW_TIMEOUT_SEC) -> bool:
    """Ask ``house`` to drop this device's request (another house approved
    it). Authenticated by this house's token — the one the request was
    parked with. Best effort: True when the house said it is gone."""

    async def ask() -> bool:
        async with connect(_stream_url(house, room_id)) as ws:
            await ws.send(json.dumps({
                "type": "hello", "room_id": room_id, "pairing_token": token,
                "approval_withdraw": True,
            }))
            frame = await _recv_json(ws)
            return bool(frame.get("type") == "error"
                        and frame.get("reason") == "approval_withdrawn"
                        and frame.get("withdrawn"))

    try:
        return await asyncio.wait_for(ask(), timeout)
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
        visit_timeout: float | None = None,
        lock_wait: float | None = None,
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
        self._visit_timeout = visit_timeout
        self._lock_wait = lock_wait    # None: ANSWER_LOCK_WAIT_SEC, read when used
        self.budget = budget
        self.houses: list[House] = []
        self.legacy: list[House] = []
        # Every house a request was parked with: who to withdraw from.
        self.asked: dict[str, House] = {}
        self._last_discovery: float | None = None
        self._rotation = 0
        self._lock = asyncio.Lock()
        self._done = asyncio.Event()

    def token_for(self, house: House) -> str:
        return house_token(self.master_token, house.fingerprint or "")

    async def discover_now(self) -> None:
        from satellite import discovery

        found = await asyncio.to_thread(self._discover)
        self._last_discovery = self._clock()
        chosen = discovery.choose_houses(found, cap=_max_houses(), rotation=self._rotation)
        self._rotation += 1
        self.houses = [h for h in chosen if h.proof and h.fingerprint]
        self.legacy = [h for h in chosen if not h.proof]
        log.info(
            "found %d Domovoi server(s) that can prove an approval%s",
            len(self.houses),
            f", and {len(self.legacy)} that cannot" if self.legacy else "",
        )

    def _release_if_acquired(self, task: asyncio.Future) -> None:
        if not task.cancelled() and task.exception() is None and task.result():
            self._lock.release()

    async def _may_answer(self) -> bool:
        """The lock that lets one exchange run at a time, waited for at
        most ``lock_wait``; False (decline) on a timeout or once a house
        has approved."""
        task = asyncio.ensure_future(self._lock.acquire())
        try:
            wait = ANSWER_LOCK_WAIT_SEC if self._lock_wait is None else self._lock_wait
            done, _ = await asyncio.wait({task}, timeout=wait)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(self._release_if_acquired)
            raise
        if not done:
            task.cancel()
            task.add_done_callback(self._release_if_acquired)
            return False
        if self._done.is_set():
            self._lock.release()
            return False
        return True

    async def _visit_one(self, house: House) -> Visit:
        if self._verify is not None:
            try:
                ok = await asyncio.wait_for(asyncio.to_thread(self._verify, house),
                                            VERIFY_TIMEOUT_SEC)
            except Exception:  # noqa: BLE001 — a timeout or a failure: not this round
                ok = False
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
                budget=self.budget, may_answer=gate, timeout=self._visit_timeout,
            )
            if result.kind == "approved":
                self._done.set()
            return result
        finally:
            if held:
                self._lock.release()

    async def round(self) -> Round:
        """Look again if it is time, then ask every house once — all at
        once, each visit bounded on its own. Returns as soon as one house
        has proved the code (the others get :data:`ROUND_GRACE_SEC` to
        finish, then are cut), so the caller adopts it at once."""
        now = self._clock()
        if self._last_discovery is None or now - self._last_discovery >= self._rediscover_every:
            await self.discover_now()
        out = Round(legacy=list(self.legacy))
        # Spent or not, every house is still visited: a visit is what keeps
        # the request alive there, and a challenge is declined, not lost.
        out.budget_spent = not self.budget.allow()
        houses = list(self.houses)
        if not houses:
            return out
        tasks = {asyncio.ensure_future(self._visit_one(h)): h for h in houses}

        def record(task: asyncio.Future) -> None:
            house = tasks[task]
            try:
                result = task.result()
            except asyncio.CancelledError:
                result = Visit("unreachable", "cut: another house approved")
            except Exception as e:  # noqa: BLE001
                result = Visit("unreachable", str(e)[:200])
            out.visits[house.url] = result
            if result.kind in ("parked", "mismatch", "approved", "declined", "bad_proof",
                               "unproven"):
                self.asked[house.fingerprint or house.url] = house
            if result.kind in ("parked", "mismatch", "declined"):
                out.parked.append(house)
            if result.kind == "approved" and out.approved is None:
                out.approved = house

        pending = set(tasks)
        try:
            while pending and out.approved is None:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    record(task)
            if pending:
                done, pending = await asyncio.wait(pending, timeout=ROUND_GRACE_SEC)
                for task in done:
                    record(task)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                for task in pending:
                    if task.done():
                        record(task)
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
