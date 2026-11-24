"""The plugin mail sender: one held connection per friend (§6.4).

Friend Houses ("serverless messaging and friend system between households"):
this module takes mail from the outbox (:mod:`domovoi.friend_houses.plugin_mail`)
and delivers it, one mail in flight per friend, on that friend's **one
plugin-lane connection** (L14). It is also the CAPS pass: the same
connection carries this house's CAPS as its first frame.

* **Gate.** Every take re-checks the *applied* internet answer (not
  "never") and that Friend Houses is on and running: never this house's
  hello, which stays set after "never".
* **Order.** The lowest outbox row id among pairs whose backoff and
  ``not_allowed`` hold have passed (L4); a pair's rows are in ``seq``
  order. The take re-checks the friend (active, apps on, speaks plugin
  mail, a fresh CAPS listing the plugin under its current epoch), the pair
  (allowed, not paused, the row's generation) and that the plugin is ready.
* **Deadlines**, overall: an answer 30 s, a drain round 120 s, a held
  connection idle 110 s (no pings), a connect 180 s. The connection
  outlives the round (L2).
* **Answers.** ACK: the row goes, and the friend's and the pair's backoffs
  reset. ``full`` / ``unavailable``: the pair backs off from the larger of
  the retry-after and 60 s / 10 s, doubling on each refusal in a row to an
  hour (L1). ``rate``, no answer, a close or a timeout: the friend backs
  off (2, 5, 15, 60, 300 s, then doubling to an hour, never below a
  ``rate`` retry-after), which ends the round; only an ACK resets it.
  ``not_allowed`` deletes only that mail and holds the pair 10 minutes;
  the pair's other mail goes only when the friend's CAPS lacks the plugin
  or a second ``not_allowed`` comes 5+ minutes after the first (M2).
* **Caps** (persisted in ``friend_houses_plugin_sender``, L2): at most 30
  dials an hour per friend (the CAPS pass included), at most 8 held
  connections per house; at a cap, mail waits. A resend spends the five
  sending budgets again (L1).
* **Stopping.** The senders are stopped with the rest of Friend Houses
  (``service._stop_retries``) and each closes its own held connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import plugin_mail as pm
from domovoi.friend_houses import plugin_wire as pw
from domovoi.friend_houses import session as fs

log = logging.getLogger(__name__)

ACK_SEC = pw.ACK_DEADLINE_SEC     # 30
ROUND_SEC = 120.0
IDLE_SEC = 110.0
CONNECT_SEC = 180.0
DIALS_PER_HOUR = 30
HELD_MAX = 8
FRIEND_STEPS = (2, 5, 15, 60, 300)
BACKOFF_MAX = 3600
PAIR_FLOOR_FULL, PAIR_FLOOR_UNAVAILABLE = 60, 10
NA_HOLD_SEC, NA_SECOND_AFTER_SEC = 600, 300
LEGACY_CLOSE_GAP_SEC = 300
PROBE_LEGACY_SEC, PROBE_MIN_SEC = pw.PROBE_LEGACY_SEC, pw.PROBE_MIN_SEC
CAPS_REFRESH_SEC = pw.REFRESH_SEC  # 3 h
TTL_SEND_MIN = fe.PLUGIN_TTL_MIN

#: ``await DIAL(friend) -> Session``: a seam the tests replace with loopback.
Dial = Callable[[dict[str, Any]], Awaitable[fs.Session]]


async def _dial_tor(friend: dict[str, Any]) -> fs.Session:
    from domovoi.friend_houses import connect as fc
    from domovoi.friend_houses import service

    keys = fid.load()
    hello = await service.my_hello()
    if keys is None or hello is None:
        raise fc.Unreachable("Friend Houses isn't running")
    return await fc.open_session(service.socks_path(), onion=fid.onion_address(friend["onion_key"]),
                                 keys=keys, me=hello,
                                 friend=fs.Friend(friend["house_key"], friend["session_key"]),
                                 timeout=CONNECT_SEC)


DIAL: Dial = _dial_tor
_SENDERS: dict[str, "_Sender"] = {}
_HELD = {"n": 0}


def gate_open() -> bool:
    """The applied internet answer isn't "never", Friend Houses is on, and
    its private connection was last reconciled to run."""
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.friend_houses import service

    return (egress.policy() != "never" and bool(getattr(settings, "friend_houses_enabled", False))
            and service.tor_wanted())


def next_friend_backoff(current: int, retry_after: int = 0) -> int:
    """2, 5, 15, 60, 300, then doubling to an hour; never below ``retry_after``."""
    if current <= 0:
        step = FRIEND_STEPS[0]
    elif current < FRIEND_STEPS[-1]:
        step = next((s for s in FRIEND_STEPS if s > current), FRIEND_STEPS[-1])
    else:
        step = min(current * 2, BACKOFF_MAX)
    return max(step, min(int(retry_after), 86_400))


def next_pair_backoff(current: int, reason: fs.Nack, retry_after: int) -> int:
    """From the larger of the retry-after and 60 s (``full``) or 10 s
    (``unavailable``), doubling on each refusal in a row to an hour, never
    below the retry-after (L1)."""
    floor = PAIR_FLOOR_FULL if reason is fs.Nack.FULL else PAIR_FLOOR_UNAVAILABLE
    nxt = max(floor, retry_after) if current <= 0 else min(current * 2, BACKOFF_MAX)
    return max(nxt, min(int(retry_after), 86_400))


# ─── waking and stopping ─────────────────────────────────────────────────


def wake(friend_id: str) -> None:
    """There may be mail or a CAPS for this friend: its sender runs a round."""
    if not gate_open():
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    sender = _SENDERS.get(friend_id)
    if sender is None or sender.task is None or sender.task.done():
        sender = _SENDERS[friend_id] = _Sender(friend_id)
        sender.start()
    sender.event.set()


def close(friend_id: str) -> None:
    """A closure: the friend's sender drops its connection now."""
    sender = _SENDERS.get(friend_id)
    if sender is not None:
        sender.drop()


def stop_all() -> None:
    """Go-dark, Friend Houses off, core teardown: every sender stops and
    closes its own held connection."""
    for sender in list(_SENDERS.values()):
        if sender.task is not None and not sender.task.done():
            sender.task.cancel()
        sender.drop()
    _SENDERS.clear()


async def caps_pass() -> None:
    """Every 5 minutes: friends whose CAPS is due (``caps_needed``, the 3-h
    refresh, an unknown or due-again protocol) and friends with mail."""
    if not gate_open():
        return
    async with pm._db() as s:
        rows = (await s.execute(text(
            "SELECT id FROM friend_houses_friends f WHERE state = 'active' AND ("
            "(caps_needed AND (apps_on OR coalesce(peer_proto, 0) >= 2)) "
            "OR (apps_on AND (caps_sent_at IS NULL OR caps_sent_at < now() - make_interval(secs => CAST(:r AS integer))) "
            "AND EXISTS (SELECT 1 FROM friend_houses_plugin_pairs p WHERE p.friend_id = f.id AND p.allowed)) "
            "OR EXISTS (SELECT 1 FROM friend_houses_plugin_outbox o WHERE o.friend_id = f.id))"),
            {"r": CAPS_REFRESH_SEC})).all()
        if not rows:
            # Friends no longer told anything: their stale flag goes quietly.
            await s.execute(text(
                "UPDATE friend_houses_friends SET caps_needed = FALSE WHERE caps_needed AND NOT apps_on "
                "AND coalesce(peer_proto, 0) < 2"))
    for (friend_id,) in rows:
        wake(friend_id)


# ─── one friend's sender ─────────────────────────────────────────────────


class _Sender:
    def __init__(self, friend_id: str) -> None:
        self.friend_id = friend_id
        self.event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.sess: fs.Session | None = None
        self.sess_caps: tuple[str, ...] | None = None   # what this connection's CAPS said
        self.last_used = 0.0

    def start(self) -> None:
        self.task = pm._track(self.run(), "friend-houses-plugin-sender")

    def drop(self) -> None:
        sess, self.sess, self.sess_caps = self.sess, None, None
        if sess is not None:
            _HELD["n"] = max(0, _HELD["n"] - 1)
            pm._track(sess.close(), "friend-houses-plugin-sender-close")

    async def run(self) -> None:
        try:
            while True:
                self.event.clear()
                try:
                    wait = await asyncio.wait_for(self.round(), ROUND_SEC + ACK_SEC + CONNECT_SEC)
                except asyncio.TimeoutError:
                    self.drop()
                    wait = 5.0
                if wait is None and self.sess is None:
                    return  # nothing to do and nothing held
                timeout = IDLE_SEC if wait is None else min(float(wait), IDLE_SEC)
                try:
                    await asyncio.wait_for(self.event.wait(), max(0.05, timeout))
                except asyncio.TimeoutError:
                    if self.sess is not None and time.monotonic() - self.last_used >= IDLE_SEC:
                        self.drop()  # held idle 110 s, no pings
                        if wait is None:
                            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — woken again later
            log.exception("Friend Houses: a plugin mail sender failed")
        finally:
            self.drop()
            if _SENDERS.get(self.friend_id) is self:
                _SENDERS.pop(self.friend_id, None)

    # ── one drain round ──
    async def round(self) -> float | None:
        """Send until the queue is empty or 120 s passed. Returns seconds
        until something is due again, or None when nothing is."""
        deadline = time.monotonic() + ROUND_SEC
        while time.monotonic() < deadline:
            if not gate_open():
                self.drop()
                return None
            async with pm._db() as s:
                friend = await pm.friend_row(s, friend_id=self.friend_id)
                sender = (await s.execute(text(
                    "SELECT retry_at FROM friend_houses_plugin_sender WHERE friend_id = :f"),
                    {"f": self.friend_id})).first()
                caps = await pm.caps_slugs(s, friend) if friend else []
            if friend is None or friend["state"] != "active":
                self.drop()
                return None
            retry_at = pm._ts(sender[0]) if sender else None
            if retry_at and retry_at > pm._now():
                self.drop()
                return retry_at - pm._now()
            caps_due = self._caps_due(friend, tuple(caps))
            if self.sess is not None and (self.sess_caps != tuple(caps) or friend["caps_needed"]):
                # A CAPS to send (changed, or due again after a Resume or an
                # upgrade) needs a new connection: one CAPS each (L14).
                self.drop()
            taken = await self._take(friend) if friend["apps_on"] else None
            if isinstance(taken, (int, float)):
                return float(taken)
            if taken is None and not caps_due:
                return None
            if self.sess is None:
                opened = await self._open(friend, tuple(caps))
                if opened is not None or self.sess is None:
                    if taken is not None:
                        await self._requeue(taken["id"])
                    return opened
            if taken is None:
                return None  # the CAPS went (or was due on a held one): hold it idle
            outcome = await self._send_one(friend, taken)
            if outcome is not None:
                return outcome
        return 0.0  # a round ended with more to send: next round, same connection

    def _caps_due(self, friend: dict[str, Any], caps: tuple[str, ...]) -> bool:
        now = pm._now()
        proto = friend["peer_proto"]
        if not friend["apps_on"]:
            return bool(friend["caps_needed"] and (proto or 0) >= 2)
        if proto is not None and proto < fs.PROTO:
            probe = pm._ts(friend["probe_at"])
            return probe is None or now - probe >= PROBE_LEGACY_SEC
        if proto is None:
            probe = pm._ts(friend["probe_at"])
            return probe is None or now - probe >= PROBE_MIN_SEC or friend["caps_needed"]
        sent = pm._ts(friend["caps_sent_at"])
        return bool(friend["caps_needed"]) or (bool(caps) and (sent is None or now - sent >= CAPS_REFRESH_SEC))

    async def _take(self, friend: dict[str, Any]) -> dict[str, Any] | float | None:
        """The next mail, marked in flight; a wait in seconds; or None."""
        if (friend["peer_proto"] or 0) < fs.PROTO:
            return None
        slugs = [r for r in {*friend["peer_caps"]} if await pm.plugin_state(r) == "ready"]
        if not slugs:
            return None
        async with pm._db() as s:
            row = (await s.execute(text(
                "SELECT o.id, o.slug, o.mail_id, o.seq, o.payload, o.expires_at, o.attempts, o.size "
                "FROM friend_houses_plugin_outbox o JOIN friend_houses_plugin_pairs p "
                "ON p.friend_id = o.friend_id AND p.slug = o.slug JOIN friend_houses_friends f ON f.id = o.friend_id "
                "WHERE o.friend_id = :f AND o.state = 'queued' AND o.generation = p.generation AND p.allowed "
                "AND (p.paused_until IS NULL OR p.paused_until <= now()) "
                "AND (p.pair_retry_at IS NULL OR p.pair_retry_at <= now()) "
                "AND (p.na_hold_until IS NULL OR p.na_hold_until <= now()) "
                "AND f.state = 'active' AND f.apps_on AND f.peer_proto >= 2 AND o.slug = ANY(f.peer_caps) "
                "AND f.peer_caps_at > now() - make_interval(secs => CAST(:fresh AS integer)) AND f.peer_caps_epoch = f.peer_epoch "
                "AND o.slug = ANY(:ready) ORDER BY o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED"),
                {"f": self.friend_id, "fresh": pm.CAPS_FRESH_SEC, "ready": slugs})).first()
            if row is None:
                return None
            left = int(row[5].timestamp() - pm._now())
            if left < TTL_SEND_MIN:
                # Not worth sending: it expires here instead (L13b).
                await s.execute(text("DELETE FROM friend_houses_plugin_outbox WHERE id = :i"), {"i": row[0]})
                return 0.0
            if int(row[6]) > 0:
                wait = await pm.spend_send_budget(s, self.friend_id, row[1], int(row[7]))  # L1
                if wait:
                    return float(wait)
            await s.execute(text(
                "UPDATE friend_houses_plugin_outbox SET state = 'inflight', attempts = attempts + 1 "
                "WHERE id = :i"), {"i": row[0]})
        return {"id": int(row[0]), "slug": row[1], "mail_id": bytes(row[2]), "seq": int(row[3]),
                "payload": bytes(row[4]), "ttl": min(left, fe.PLUGIN_TTL_MAX)}

    async def _requeue(self, row_id: int) -> None:
        async with pm._db() as s:
            await s.execute(text(
                "UPDATE friend_houses_plugin_outbox SET state = 'queued' WHERE id = :i AND state = 'inflight'"),
                {"i": row_id})

    # ── the connection ──
    async def _open(self, friend: dict[str, Any], caps: tuple[str, ...]) -> float | None:
        """Dial (within the caps), CAPS first, then a PING: what that says
        about the friend (L8). Returns a wait when the connection couldn't be
        made or kept, else None with ``self.sess`` held."""
        if _HELD["n"] >= HELD_MAX:
            return 30.0
        wait = await self._note_dial()
        if wait:
            return wait
        try:
            sess = await asyncio.wait_for(DIAL(friend), CONNECT_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — unreachable: the friend backs off
            log.info("Friend Houses: a friend house couldn't be reached for apps: %s", str(e)[:120])
            return await self._friend_backoff()
        _HELD["n"] += 1
        self.sess, self.sess_caps, self.last_used = sess, caps, time.monotonic()
        try:
            outcome = await pw.offer(sess, pw.encode_caps(caps), known=pw.Known())
        except Exception:  # noqa: BLE001
            outcome = pw.Offer.NO_ANSWER
        await self._probed(friend, outcome, caps)
        if outcome is not pw.Offer.DELIVERED:
            self.drop()
            return await self._friend_backoff()
        if not friend["apps_on"]:
            self.drop()  # the one empty CAPS went; nothing else for this friend
            return None
        return None

    async def _note_dial(self) -> float:
        """Record a dial (at most 30 an hour per friend, persisted): 0, or
        seconds until one is allowed."""
        now = pm._now()
        async with pm._db() as s:
            await s.execute(text(
                "INSERT INTO friend_houses_plugin_sender (friend_id) VALUES (:f) ON CONFLICT DO NOTHING"),
                {"f": self.friend_id})
            row = (await s.execute(text(
                "SELECT dials FROM friend_houses_plugin_sender WHERE friend_id = :f FOR UPDATE"),
                {"f": self.friend_id})).first()
            dials = row[0] if isinstance(row[0], list) else json.loads(row[0] or "[]")
            dials = [float(t) for t in dials if now - float(t) < 3600][-DIALS_PER_HOUR:]
            if len(dials) >= DIALS_PER_HOUR:
                return max(1.0, dials[0] + 3600 - now)
            dials.append(int(now))
            await s.execute(text(
                "UPDATE friend_houses_plugin_sender SET dials = CAST(:d AS jsonb) WHERE friend_id = :f"),
                {"d": json.dumps(dials), "f": self.friend_id})
        return 0.0

    async def _probed(self, friend: dict[str, Any], outcome: pw.Offer, caps: tuple[str, ...]) -> None:
        """L8: a PONG means plugin mail (and, when it is still the CAPS this
        house wants to say, ``caps_needed`` clears); a close is one legacy
        sign, and legacy only after two closes 5+ minutes apart; no answer
        (a send error included) decides nothing."""
        async with pm._db() as s:
            current = await pm.friend_row(s, friend_id=self.friend_id, lock=True)
            if current is None:
                return
            if outcome is pw.Offer.DELIVERED:
                still = tuple(await pm.caps_slugs(s, current)) == caps
                await s.execute(text(
                    "UPDATE friend_houses_friends SET peer_proto = greatest(coalesce(peer_proto, 0), 2), "
                    "probe_at = now(), probe_closes = 0, first_close_at = NULL, "
                    "caps_needed = CASE WHEN :still THEN FALSE ELSE caps_needed END, "
                    "caps_sent_at = CASE WHEN :still THEN now() ELSE caps_sent_at END WHERE id = :i"),
                    {"still": still, "i": self.friend_id})
            elif outcome is pw.Offer.LEGACY:
                first = pm._ts(current["first_close_at"])
                if first is None or not current["probe_closes"]:
                    await s.execute(text(
                        "UPDATE friend_houses_friends SET probe_closes = 1, first_close_at = now(), "
                        "probe_at = now() WHERE id = :i"), {"i": self.friend_id})
                elif pm._now() - first >= LEGACY_CLOSE_GAP_SEC:
                    await s.execute(text(
                        "UPDATE friend_houses_friends SET peer_proto = 1, probe_closes = 0, "
                        "first_close_at = NULL, probe_at = now() WHERE id = :i"), {"i": self.friend_id})
                    log.info("Friend Houses: a friend house runs a Domovoi before 1.5.0 (no apps)")

    async def _friend_backoff(self, retry_after: int = 0) -> float:
        async with pm._db() as s:
            await s.execute(text(
                "INSERT INTO friend_houses_plugin_sender (friend_id) VALUES (:f) ON CONFLICT DO NOTHING"),
                {"f": self.friend_id})
            cur = (await s.execute(text(
                "SELECT backoff_sec FROM friend_houses_plugin_sender WHERE friend_id = :f FOR UPDATE"),
                {"f": self.friend_id})).scalar_one()
            nxt = next_friend_backoff(int(cur), retry_after)
            await s.execute(text(
                "UPDATE friend_houses_plugin_sender SET backoff_sec = :b, "
                "retry_at = now() + make_interval(secs => CAST(:b AS integer)) WHERE friend_id = :f"),
                {"b": nxt, "f": self.friend_id})
        return float(nxt)

    # ── one mail ──
    async def _send_one(self, friend: dict[str, Any], mail: dict[str, Any]) -> float | None:
        from domovoi.friend_houses import service

        keys = fid.load()
        hello = await service.my_hello()
        if keys is None or hello is None or self.sess is None:
            await self._requeue(mail["id"])
            return 30.0
        env = fe.plugin_mail(keys, to=friend["house_key"], slug=mail["slug"], payload=mail["payload"],
                             ttl_s=mail["ttl"], epoch=hello.epoch, seq=mail["seq"],
                             sent_at=int(time.time()), msg_id=mail["mail_id"])
        try:
            await self.sess.send(fs.Kind.MESSAGE, env.to_bytes())
            answer = await pw.await_answer(self.sess, mail["mail_id"], deadline=ACK_SEC)
        except (ConnectionError, OSError, fs.SessionRefused):
            answer = None
        self.last_used = time.monotonic()
        if answer is None:
            await self._requeue(mail["id"])
            self.drop()
            return await self._friend_backoff()
        if answer.acked:
            async with pm._db() as s:
                await s.execute(text("DELETE FROM friend_houses_plugin_outbox WHERE id = :i"), {"i": mail["id"]})
                await s.execute(text(
                    "UPDATE friend_houses_plugin_sender SET backoff_sec = 0, retry_at = NULL WHERE friend_id = :f"),
                    {"f": self.friend_id})
                await s.execute(text(
                    "UPDATE friend_houses_plugin_pairs SET pair_backoff_sec = 0, pair_retry_at = NULL "
                    "WHERE friend_id = :f AND slug = :s"), {"f": self.friend_id, "s": mail["slug"]})
            return None
        reason, secs = answer.reason, int(answer.retry_after)
        if reason is fs.Nack.NOT_ALLOWED:
            await self._not_allowed(mail)
            return None
        await self._requeue(mail["id"])
        if reason is fs.Nack.RATE:
            self.drop()
            return await self._friend_backoff(secs)
        # full / unavailable: this pair backs off; others carry on (L1).
        async with pm._db() as s:
            cur = (await s.execute(text(
                "SELECT pair_backoff_sec FROM friend_houses_plugin_pairs WHERE friend_id = :f AND slug = :s "
                "FOR UPDATE"), {"f": self.friend_id, "s": mail["slug"]})).scalar_one_or_none()
            if cur is not None:
                nxt = next_pair_backoff(int(cur), reason, secs)
                await s.execute(text(
                    "UPDATE friend_houses_plugin_pairs SET pair_backoff_sec = :b, "
                    "pair_retry_at = now() + make_interval(secs => CAST(:b AS integer)) WHERE friend_id = :f AND slug = :s"),
                    {"b": nxt, "f": self.friend_id, "s": mail["slug"]})
        return None

    async def _not_allowed(self, mail: dict[str, Any]) -> None:
        """M2: this mail goes; the pair is held 10 minutes. A second
        ``not_allowed`` 5+ minutes after the first also deletes the pair's
        other mail and forgets that the friend has the plugin."""
        slug = mail["slug"]
        async with pm._db() as s:
            await s.execute(text("DELETE FROM friend_houses_plugin_outbox WHERE id = :i"), {"i": mail["id"]})
            pair = await pm.pair_row(s, self.friend_id, slug, lock=True)
            if pair is None:
                return
            first = pm._ts(pair["na_first_at"])
            if first is not None and pm._now() - first >= NA_SECOND_AFTER_SEC:
                await s.execute(text(
                    "DELETE FROM friend_houses_plugin_outbox WHERE friend_id = :f AND slug = :s"),
                    {"f": self.friend_id, "s": slug})
                await s.execute(text(
                    "UPDATE friend_houses_friends SET peer_caps = array_remove(peer_caps, :s) WHERE id = :f"),
                    {"f": self.friend_id, "s": slug})
                await s.execute(text(
                    "UPDATE friend_houses_plugin_pairs SET na_first_at = NULL, na_hold_until = NULL "
                    "WHERE friend_id = :f AND slug = :s"), {"f": self.friend_id, "s": slug})
                log.info("Friend Houses: a friend house refused %s twice; its queued mail was dropped", slug)
            elif first is None:
                await s.execute(text(
                    "UPDATE friend_houses_plugin_pairs SET na_first_at = now(), "
                    "na_hold_until = now() + make_interval(secs => CAST(:h AS integer)) WHERE friend_id = :f AND slug = :s"),
                    {"h": NA_HOLD_SEC, "f": self.friend_id, "s": slug})


__all__ = ["DIAL", "caps_pass", "close", "gate_open", "next_friend_backoff", "next_pair_backoff",
           "stop_all", "wake"]
