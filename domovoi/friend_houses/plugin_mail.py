"""Plugin mail: "apps with friends" between two houses' copies of a plugin.

Friend Houses ("serverless messaging and friend system between households")
carries mail between the same plugin in two friend houses (SDK 1.18, design
note ``FRIEND-HOUSES-PLUGINS`` §6, build slice 4). This module is the
service: the toggle and the pairs, the outbox and the inbox, the receiving
lane, the decode-and-handle dispatcher, the breaker's persistence and every
closure. :mod:`domovoi.friend_houses.plugin_sender` sends; the wire is
:mod:`domovoi.friend_houses.plugin_wire`.

**Plugin mail never reaches people's messages** (review D12): the
listener's ``plugin_mail`` event comes here only, never to
:func:`domovoi.friend_houses.messages.on_message`, and nothing here writes
``friend_houses_messages``.

Who may mail whom
-----------------
Only friends an admin turned **"Apps with this friend"** on for
(``apps_on``), and per friend only the plugins allowed for it
(``friend_houses_plugin_pairs.allowed``). A plugin installed later with the
permission (or upgraded to gain it) starts **disallowed for every friend**
until an admin confirms it. A pair row is never deleted while its friend
lives: a closure sets ``allowed = false`` and bumps ``generation``.

Quotas (§6.2)
-------------
Outbox and inbox each: 125 rows / 1 MiB per (friend, plugin), 1,000 / 8 MiB
per plugin, 4,000 / 32 MiB per house; every pair keeps a floor of 16 rows /
128 KiB the plugin and house totals can't take (L5). Over: ``MailFull`` at
the outbox, ``MESSAGE_NACK full`` at the inbox. Mail expires: the sender
puts the **remaining** time on the wire (L13b), the receiver keeps it at
most that long, and a sweep every minute deletes expired rows in every
state but ``handling``.

Sequence and epoch (§6.5, M1, L3, L13g)
---------------------------------------
``seq = out_seq = max(out_seq + 1, unix ms)``, so a rolled-back counter
jumps forward to the clock. The receiver accepts an envelope only when its
epoch is the connection's hello epoch and not retired; a new epoch retires
the old one (at most 16 kept, never accepted again) and starts every pair's
mark at 0. Under a row lock on the pair: ``seq <= in_mark`` is ACKed and
dropped; else the row is stored and ``in_mark = greatest(in_mark, seq)``.

Handling (§6.7, H1, M3, M4, M5)
-------------------------------
``stored -> decoding -> decoded -> handling -> deleted``. The move into
``decoding`` or ``handling`` records the attempt **before** the decoder or
the handler runs (H1): a clean shutdown puts those rows back uncharged, so
a row found there at the next start means the core stopped while working
on it, and it is charged (``decoding`` weight 1, ``handling`` weight 3,
``faults + 1``; quarantined at 2). A handler gets 10 s; after that core
stops waiting, charges weight 1, frees the slot and ignores a late result.
Every string in the value reaches the handler as a
:class:`~domovoi.sdk.friend_houses.FriendText`, on every delivery.

The breaker per (friend, plugin) lives in the pair row
(:class:`domovoi.plugins_runtime.isolated.Breaker`, integer hundredths, on
the wall clock); this module is the decoders' charge sink.

Lock order
----------
Every transaction that touches these tables takes its row locks in this
order, and only in this order (it may skip a level, never go back up one):

1. **friend rows** (``friend_houses_friends``), ``FOR NO KEY UPDATE``;
   several at once in ``id`` order (:func:`lock_friends`). Only a
   transaction that deletes the friend (unfriend, block) takes
   ``FOR UPDATE`` (:func:`lock_friend_for_delete`), first.
2. **pair rows** (``friend_houses_plugin_pairs``), ``FOR NO KEY UPDATE``;
   several at once in ``(friend_id, slug)`` order (:func:`lock_pairs`).
3. **the friend's sender row** (``friend_houses_plugin_sender``).
4. **the house's state row** (``friend_houses_state``: the house budgets).
5. **outbox and inbox rows**.
6. **the quota advisory lock** (:func:`quota_ok`), just before the insert.

``FOR NO KEY UPDATE``, never ``FOR UPDATE``, on a row whose keys don't
change: every insert into a child table (pairs, sender, outbox, inbox)
checks its foreign key with a ``KEY SHARE`` lock on the friend row, which
``FOR UPDATE`` blocks and ``FOR NO KEY UPDATE`` doesn't. A ``FOR UPDATE``
friend lock there, with the pair locked in the other order by ``send``,
was a deadlock (store_mail vs send for one friend). A plain ``UPDATE`` of
a friend row takes ``FOR NO KEY UPDATE`` by itself, so it counts as level 1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from domovoi.friend_houses import budget as fb
from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid

log = logging.getLogger(__name__)

KIB, MIB = 1024, 1024 * 1024
PAIR_ROWS, PAIR_BYTES = 125, 1 * MIB
PLUGIN_ROWS, PLUGIN_BYTES = 1_000, 8 * MIB
HOUSE_ROWS, HOUSE_BYTES = 4_000, 32 * MIB
FLOOR_ROWS, FLOOR_BYTES = 16, 128 * KIB
TTL_MIN, TTL_MAX = fe.PLUGIN_TTL_MIN, fe.PLUGIN_TTL_MAX
PAYLOAD_MAX = fe.PLUGIN_PAYLOAD_MAX

SWEEP_EVERY_SEC = 60.0
CAPS_PASS_EVERY_SEC = 300.0
HANDLING_STALE_SEC = 60
HANDLER_DEADLINE_SEC = 10.0
LANE_DEPTH, LANE_WORKERS = 64, 2
LANE_FULL_RETRY_SEC = 10
FULL_RETRY_SEC = 60
NOT_LOADED_RETRY_SEC = 60
RETIRED_MAX = 16
CAPS_FRESH_SEC = 6 * 3600
WAIT_RETRY_SEC = 30          # an item that waits (not loaded, no schema, decoder busy)
FAULT_RETRY_SEC = 30         # an item that faulted, before its next try
ITEM_FAULTS_MAX = 2
PER_PLUGIN_AT_ONCE = 4       # the decoder's slots
SPAWN_MAX = 16               # CAPS / connected events in flight

_ADVISORY = {"outbox": 0x46480001, "inbox": 0x46480002}


def _db():
    from domovoi.db.session import session_scope

    return session_scope()


def _service():
    from domovoi.friend_houses import service

    return service


def _iso():
    from domovoi.plugins_runtime import isolated

    return isolated


def _now() -> float:
    return time.time()


def _ts(value: Any) -> float | None:
    return value.timestamp() if isinstance(value, datetime) else None


def _dt(unix: float | None) -> datetime | None:
    return datetime.fromtimestamp(unix, tz=timezone.utc) if unix else None


# ─── which plugins may take part (L12) ────────────────────────────────────


async def plugin_state(slug: str) -> str:
    """``ready`` (enabled, loaded, its mail handler registered, holding the
    permission), ``not_loaded`` (enabled and holding the permission, but not
    running yet: an upgrade waiting for its restart, or no handler yet) or
    ``off`` (disabled, uninstalled, or without the permission)."""
    from domovoi.plugins_runtime.loader import LOADER
    from domovoi.sdk.friend_houses import registered

    lp = LOADER.loaded.get(slug)
    if lp is not None and slug not in LOADER.pending_restart:
        if not lp.manifest.permissions.get("friend_houses"):
            return "off"
        api = registered(slug)
        ready = api is not None and api.mail_handler is not None and api.schema is not None
        return "ready" if ready else "not_loaded"
    return "not_loaded" if slug in await permitted_slugs() else "off"


async def permitted_slugs() -> list[str]:
    """Enabled plugins whose manifest holds ``[permissions] friend_houses``
    (loaded or not)."""
    from domovoi.plugins_runtime import registry as reg

    try:
        rows = await reg.list_plugins()
    except Exception:  # noqa: BLE001 — no registry, no apps
        log.exception("Friend Houses: the plugin list can't be read")
        return []
    out = []
    for r in rows:
        if r.enabled and r.status != "uninstalled" and fe.valid_slug(r.slug) and \
                bool(((r.manifest or {}).get("permissions") or {}).get("friend_houses")):
            out.append(r.slug)
    return sorted(out)


async def plugin_names() -> dict[str, str]:
    """slug -> the plugin's display name (its manifest's), for the admin's
    views and notices. Never raises."""
    from domovoi.plugins_runtime import registry as reg

    try:
        rows = await reg.list_plugins()
    except Exception:  # noqa: BLE001 — slugs stand in
        return {}
    return {r.slug: str(r.name or r.slug)[:80] for r in rows}


# ─── what a plugin is told ────────────────────────────────────────────────


def wrap(value: Any) -> Any:
    """``value`` with every string leaf as a FriendText (M5): list items
    and object values; object keys stay plain (the schema named them)."""
    from domovoi.sdk.friend_houses import FriendText

    if isinstance(value, str):
        return FriendText(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    if isinstance(value, dict):
        return {k: wrap(v) for k, v in value.items()}
    return value


def delivered_value(canonical: bytes, kind: int, image_png: Any = None) -> Any:
    """What the handler gets, made again on every delivery (retries after a
    restart included): a data mail's value with every string a FriendText
    (M5); an image mail's :class:`~domovoi.sdk.friend_houses.FriendImage`,
    the core's own PNG with its tag as a FriendText (D1)."""
    value = json.loads(canonical.decode("utf-8"))
    if kind != fe.PLUGIN_BODY_IMAGE:
        return wrap(value)
    from domovoi.sdk.friend_houses import FriendImage, FriendText

    info = value["image"]
    return FriendImage(bytes(image_png or b""), int(info["width"]), int(info["height"]),
                       FriendText(str(info.get("tag", ""))))


def has_plugin(friend: dict[str, Any], slug: str, now: float | None = None) -> bool:
    """The friend's last CAPS listed ``slug``, within 6 h, under its
    current epoch (§6.8)."""
    now = _now() if now is None else now
    at = _ts(friend.get("peer_caps_at"))
    return bool(
        (friend.get("peer_proto") or 0) >= 2 and slug in (friend.get("peer_caps") or [])
        and at is not None and now - at <= CAPS_FRESH_SEC
        and friend.get("peer_caps_epoch") is not None
        and friend.get("peer_caps_epoch") == friend.get("peer_epoch"))


_FRIEND_COLS = ("id, house_key, state, apps_on, caps_needed, caps_sent_at, peer_proto, peer_caps, "
                "peer_caps_at, peer_caps_epoch, probe_at, probe_closes, first_close_at, peer_epoch, "
                "peer_epochs_retired, local_name, onion_key, session_key, last_seen_at, apps_on_at")


def _friend(r: Any) -> dict[str, Any]:
    keys = [c.strip() for c in _FRIEND_COLS.split(",")]
    out = dict(zip(keys, r))
    for k in ("house_key", "peer_caps_epoch", "peer_epoch", "onion_key", "session_key"):
        out[k] = bytes(out[k]) if out[k] is not None else None
    out["peer_epochs_retired"] = [bytes(e) for e in (out["peer_epochs_retired"] or [])]
    out["peer_caps"] = list(out["peer_caps"] or [])
    return out


async def friend_row(s, *, friend_id: str | None = None, house: bytes | None = None,
                     lock: bool = False) -> dict[str, Any] | None:
    """One friend row; with ``lock``, locked ``FOR NO KEY UPDATE`` (lock
    order level 1, see the module docstring)."""
    where = "id = :k" if friend_id is not None else "house_key = :k"
    row = (await s.execute(text(
        f"SELECT {_FRIEND_COLS} FROM friend_houses_friends WHERE {where}"
        + (" FOR NO KEY UPDATE" if lock else "")),
        {"k": friend_id if friend_id is not None else house})).first()
    return _friend(row) if row else None


async def lock_friends(s, where: str, params: dict[str, Any] | None = None) -> list[str]:
    """Lock the friend rows matching ``where`` ``FOR NO KEY UPDATE``, in
    ``id`` order (lock order level 1); their ids."""
    return [r[0] for r in await s.execute(text(
        f"SELECT id FROM friend_houses_friends WHERE {where} ORDER BY id FOR NO KEY UPDATE"),
        params or {})]


async def lock_friend_for_delete(s, friend_id: str) -> None:
    """The friend row ``FOR UPDATE``, first in a transaction that deletes it
    (unfriend, block): its key goes, so this one may block foreign key
    checks, and taking it first means it is never an upgrade."""
    await s.execute(text("SELECT 1 FROM friend_houses_friends WHERE id = :i FOR UPDATE"),
                    {"i": friend_id})


async def lock_pairs(s, where: str, params: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    """Lock the pair rows matching ``where`` ``FOR NO KEY UPDATE``, in
    ``(friend_id, slug)`` order (lock order level 2); their keys."""
    return [(r[0], r[1]) for r in await s.execute(text(
        f"SELECT friend_id, slug FROM friend_houses_plugin_pairs WHERE {where} "
        "ORDER BY friend_id, slug FOR NO KEY UPDATE"), params or {})]


_PAIR_COLS = ("friend_id, slug, allowed, confirmed_at, generation, out_seq, in_epoch, in_mark, "
              "fault_events, refusal_times, paused_until, pause_cause, next_pause_sec, last_pause_at, "
              "pair_retry_at, pair_backoff_sec, na_hold_until, na_first_at, decode_ms_hour")


def _pair(r: Any) -> dict[str, Any]:
    keys = [c.strip() for c in _PAIR_COLS.split(",")]
    out = dict(zip(keys, r))
    out["in_epoch"] = bytes(out["in_epoch"]) if out["in_epoch"] is not None else None
    for k in ("fault_events", "refusal_times"):
        v = out[k]
        out[k] = json.loads(v) if isinstance(v, str) else list(v or [])
    return out


async def pair_row(s, friend_id: str, slug: str, *, lock: bool = False) -> dict[str, Any] | None:
    row = (await s.execute(text(
        f"SELECT {_PAIR_COLS} FROM friend_houses_plugin_pairs WHERE friend_id = :f AND slug = :s"
        + (" FOR NO KEY UPDATE" if lock else "")), {"f": friend_id, "s": slug})).first()
    return _pair(row) if row else None


async def _ensure_pair(s, friend_id: str, slug: str) -> None:
    await s.execute(text(
        "INSERT INTO friend_houses_plugin_pairs (friend_id, slug) VALUES (:f, :s) "
        "ON CONFLICT (friend_id, slug) DO NOTHING"), {"f": friend_id, "s": slug})


def _paused(pair: dict[str, Any], now: float) -> float:
    """Seconds of pause left (0: not paused)."""
    until = _ts(pair.get("paused_until"))
    return max(0.0, until - now) if until else 0.0


# ─── the breaker, persisted (§6.7; the decoders' charge sink) ────────────


def breaker_of(pair: dict[str, Any]):
    iso = _iso()
    return iso.Breaker(
        events=[list(e) for e in pair["fault_events"]], refusals=list(pair["refusal_times"]),
        paused_until=_ts(pair["paused_until"]) or 0.0, cause=pair["pause_cause"],
        next_pause=int(pair["next_pause_sec"]), last_pause_at=_ts(pair["last_pause_at"]))


async def _save_breaker(s, friend_id: str, slug: str, b) -> None:
    await s.execute(text(
        "UPDATE friend_houses_plugin_pairs SET fault_events = CAST(:e AS jsonb), "
        "refusal_times = CAST(:r AS jsonb), paused_until = :pu, pause_cause = :c, "
        "next_pause_sec = :n, last_pause_at = :lp WHERE friend_id = :f AND slug = :s"),
        {"e": json.dumps(b.events), "r": json.dumps(b.refusals), "pu": _dt(b.paused_until),
         "c": b.cause, "n": int(b.next_pause), "lp": _dt(b.last_pause_at), "f": friend_id, "s": slug})


async def charge_sink(slug: str, friend_id: str, item_key: str | None, hundredths: int, why: str,
                      kind: str):
    """Every charge to a (friend, plugin) breaker lands here and in the pair
    row, which is authoritative. Returns the breaker after it."""
    iso = _iso()
    async with _db() as s:
        pair = await pair_row(s, friend_id, slug, lock=True)
        if pair is None:
            return None
        b = breaker_of(pair)
        now = _now()
        pause = b.refusal(now) if kind == iso.KIND_REFUSAL else b.charge(now, int(hundredths), kind)
        await _save_breaker(s, friend_id, slug, b)
    log.info("Friend Houses: %s mail from a friend charged %s (%s)", slug,
             "a refusal" if kind == iso.KIND_REFUSAL else f"{hundredths / 100:g}", why[:80])
    if pause:
        iso.notify_pause(slug, friend_id, int(pause), b.cause or "faults", kind == iso.KIND_RESTART)
    return b


# ─── buckets, persisted at the sender (§5, L1, L2) ───────────────────────


def _bucket(states: dict, key: str, per_hour: float, burst: float) -> fb.Bucket:
    return fb.Bucket.restore(per_hour, burst, states.get(key))


async def lock_send_budget(s, friend_id: str) -> None:
    """Lock the friend's sender row (made if missing) and the house's state
    row, lock order levels 3 and 4: before any outbox row or the quota
    lock, in a transaction that may then spend the budgets."""
    await s.execute(text(
        "INSERT INTO friend_houses_plugin_sender (friend_id) VALUES (:f) "
        "ON CONFLICT (friend_id) DO NOTHING"), {"f": friend_id})
    await s.execute(text(
        "SELECT 1 FROM friend_houses_plugin_sender WHERE friend_id = :f FOR NO KEY UPDATE"),
        {"f": friend_id})
    await s.execute(text("SELECT 1 FROM friend_houses_state WHERE id = 1 FOR NO KEY UPDATE"))


async def spend_send_budget(s, friend_id: str, slug: str, size: int) -> int:
    """Spend the five sender buckets for one mail (at enqueue and again on
    every resend): 0, or seconds to wait (nothing spent). Locks the friend's
    sender row and the house's state row; a caller that already holds an
    outbox row or the quota lock took them first (:func:`lock_send_budget`)."""
    await s.execute(text(
        "INSERT INTO friend_houses_plugin_sender (friend_id) VALUES (:f) "
        "ON CONFLICT (friend_id) DO NOTHING"), {"f": friend_id})
    row = (await s.execute(text(
        "SELECT buckets FROM friend_houses_plugin_sender WHERE friend_id = :f FOR NO KEY UPDATE"),
        {"f": friend_id})).first()
    house = (await s.execute(text(
        "SELECT plugin_buckets FROM friend_houses_state WHERE id = 1 FOR NO KEY UPDATE"))).first()
    if row is None or house is None:
        return 60
    mine = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    ours = house[0] if isinstance(house[0], dict) else json.loads(house[0] or "{}")
    now = _now()
    pair = _bucket(mine, f"p:{slug}", fb.PAIR_PER_HOUR, fb.PAIR_BURST)
    friend = _bucket(mine, "f", fb.FRIEND_PER_HOUR, fb.FRIEND_BURST)
    fbytes = _bucket(mine, "fb", fb.FRIEND_BYTES_PER_HOUR, fb.FRIEND_BYTES_BURST)
    hcount = _bucket(ours, "h", fb.HOUSE_PER_HOUR, fb.HOUSE_BURST)
    hbytes = _bucket(ours, "hb", fb.BYTES_PER_HOUR, fb.BYTES_BURST)
    wait = fb.spend_all([(pair, 1), (friend, 1), (fbytes, size)], [(hcount, 1), (hbytes, size)], now)
    if wait:
        return wait
    mine.update({f"p:{slug}": pair.state(), "f": friend.state(), "fb": fbytes.state()})
    ours.update({"h": hcount.state(), "hb": hbytes.state()})
    await s.execute(text("UPDATE friend_houses_plugin_sender SET buckets = CAST(:b AS jsonb) "
                         "WHERE friend_id = :f"), {"b": json.dumps(mine), "f": friend_id})
    await s.execute(text("UPDATE friend_houses_state SET plugin_buckets = CAST(:b AS jsonb) "
                         "WHERE id = 1"), {"b": json.dumps(ours)})
    return 0


# ─── quotas with floors (§6.2, L5) ───────────────────────────────────────

_BYTES = {"outbox": "size",
          "inbox": "coalesce(octet_length(payload), 0) + coalesce(octet_length(canonical), 0) "
                   "+ coalesce(octet_length(image_png), 0)"}


async def quota_ok(s, table: str, friend_id: str, slug: str, size: int) -> bool:
    """Whether one more row of ``size`` bytes fits. Serialized per table by
    an advisory lock for the rest of the transaction."""
    assert table in _BYTES
    await s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY[table]})
    tbl = f"friend_houses_plugin_{table}"
    b = _BYTES[table]
    row = (await s.execute(text(
        f"SELECT count(*) FILTER (WHERE friend_id = :f AND slug = :s), "
        f"coalesce(sum({b}) FILTER (WHERE friend_id = :f AND slug = :s), 0), "
        f"count(*) FILTER (WHERE slug = :s), coalesce(sum({b}) FILTER (WHERE slug = :s), 0), "
        f"count(*), coalesce(sum({b}), 0) FROM {tbl}"), {"f": friend_id, "s": slug})).first()
    pair_n, pair_b, plug_n, plug_b, house_n, house_b = (int(x) for x in row)
    if pair_n + 1 > PAIR_ROWS or pair_b + size > PAIR_BYTES:
        return False
    if pair_n + 1 <= FLOOR_ROWS and pair_b + size <= FLOOR_BYTES:
        return True  # the pair's floor: the plugin and house totals can't take it
    return (plug_n + 1 <= PLUGIN_ROWS and plug_b + size <= PLUGIN_BYTES
            and house_n + 1 <= HOUSE_ROWS and house_b + size <= HOUSE_BYTES)


# ─── sending: the outbox (§6.4, §6.5) ────────────────────────────────────


async def send(slug: str, friend_id: str, payload: bytes, *, ttl_s: int = TTL_MAX,
               replace_key: str | None = None, version: int = fe.PLUGIN_BODY_VERSION) -> str:
    """Queue one mail from plugin ``slug`` to friend ``friend_id``; its
    mail id (hex). Raises the SDK's NotAllowed, MailFull, TooLarge or
    RateLimited (and ValueError for a bad argument). ``replace_key``
    replaces the queued (never the in-flight) mail with that key, with a
    new mail id and a new ``seq``. ``version`` 2 is an image mail (its
    payload made by :func:`envelope.image_payload`)."""
    from domovoi.sdk import friend_houses as sdk

    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise ValueError("payload is non-empty bytes")
    if version not in fe.PLUGIN_BODY_VERSIONS:
        raise ValueError("a data or an image mail")
    if version == fe.PLUGIN_BODY_IMAGE:
        try:
            fe.split_image_payload(bytes(payload))
        except fe.BadEnvelope as exc:
            raise ValueError("not an image mail's payload") from exc
    if len(payload) > PAYLOAD_MAX:
        raise sdk.TooLarge(f"payload is {len(payload)} bytes; at most {PAYLOAD_MAX}")
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, int) or not TTL_MIN <= ttl_s <= TTL_MAX:
        raise ValueError(f"ttl_s is {TTL_MIN}..{TTL_MAX} seconds")
    if replace_key is not None and not sdk.REPLACE_KEY_RE.match(str(replace_key)):
        raise ValueError("replace_key is up to 64 of a-z 0-9 _ . : -")
    if await plugin_state(slug) != "ready":
        raise sdk.NotAllowed("this plugin isn't running with apps with friends")
    mail_id = secrets.token_bytes(16)
    async with _db() as s:
        # Lock order: friend, pair, sender + house budgets, outbox, quota.
        friend = await friend_row(s, friend_id=friend_id, lock=True)
        if friend is None or friend["state"] != "active" or not friend["apps_on"]:
            raise sdk.NotAllowed("apps aren't on with that friend")
        pair = await pair_row(s, friend_id, slug, lock=True)
        if pair is None or not pair["allowed"]:
            raise sdk.NotAllowed("this plugin isn't allowed with that friend")
        await lock_send_budget(s, friend_id)
        if replace_key is not None:
            await s.execute(text(
                "DELETE FROM friend_houses_plugin_outbox WHERE friend_id = :f AND slug = :s "
                "AND replace_key = :k AND state = 'queued'"),
                {"f": friend_id, "s": slug, "k": replace_key})
        if not await quota_ok(s, "outbox", friend_id, slug, len(payload)):
            raise sdk.MailFull("this plugin's mail for that friend is full; try later")
        wait = await spend_send_budget(s, friend_id, slug, len(payload))
        if wait:
            raise sdk.RateLimited(f"over a sending budget; try in {wait} s")
        seq = (await s.execute(text(
            "UPDATE friend_houses_plugin_pairs SET out_seq = greatest(out_seq + 1, :ms) "
            "WHERE friend_id = :f AND slug = :s RETURNING out_seq"),
            {"ms": int(_now() * 1000), "f": friend_id, "s": slug})).scalar_one()
        await s.execute(text(
            "INSERT INTO friend_houses_plugin_outbox (friend_id, slug, generation, mail_id, seq, "
            "replace_key, payload, size, ttl_s, expires_at, kind) VALUES (:f, :s, :g, :m, :q, :k, :p, "
            ":n, :t, now() + make_interval(secs => CAST(:t AS integer)), :v)"),
            {"f": friend_id, "s": slug, "g": pair["generation"], "m": mail_id, "q": int(seq),
             "k": replace_key, "p": bytes(payload), "n": len(payload), "t": ttl_s, "v": int(version)})
    from domovoi.friend_houses import plugin_sender

    plugin_sender.wake(friend_id)
    return mail_id.hex()


# ─── what a plugin sees: friends() and its ids (§4, slice 5) ─────────────


async def _plugin_friends(slug: str) -> list[dict[str, Any]]:
    """The friends ``slug`` may mail: active, "Apps with this friend" on and
    the pair allowed. Each with this plugin's id for it (never stored)."""
    keys = fid.load()
    if keys is None:
        return []
    async with _db() as s:
        rows = [_friend(r) for r in await s.execute(text(
            f"SELECT {_FRIEND_COLS} FROM friend_houses_friends WHERE state = 'active' AND apps_on "
            "AND id IN (SELECT friend_id FROM friend_houses_plugin_pairs WHERE slug = :s AND allowed) "
            "ORDER BY local_name, id"), {"s": slug})]
    for f in rows:
        f["house_id"] = keys.plugin_house_id(slug, f["house_key"])
    return rows


async def friends_for(slug: str) -> list[dict[str, Any]]:
    """``sdk.friend_houses.friends()`` for plugin ``slug``: ``[{house_id,
    name, online, has_plugin}]``. Empty while the plugin isn't running with
    the permission, or while Friend Houses is off or dark (so a plugin
    hides its "play with a friend" control then: nothing could reach
    them)."""
    from domovoi.friend_houses import plugin_sender, presence

    if await plugin_state(slug) == "off" or not plugin_sender.gate_open():
        return []
    now = _now()
    return [{"house_id": f["house_id"], "name": f["local_name"] or "",
             "online": presence.online(f["last_seen_at"]), "has_plugin": has_plugin(f, slug, now)}
            for f in await _plugin_friends(slug)]


async def friend_for_house_id(slug: str, house_id: str) -> str | None:
    """The friend row id behind plugin ``slug``'s ``house_id``, among the
    friends it may mail; None otherwise."""
    for f in await _plugin_friends(slug):
        if secrets.compare_digest(f["house_id"], house_id):
            return f["id"]
    return None


async def send_to(slug: str, house_id: str, payload: bytes, *, ttl_s: int = TTL_MAX,
                  replace_key: str | None = None, version: int = fe.PLUGIN_BODY_VERSION) -> str:
    """:func:`send` by the plugin's own id for the friend (SDK
    ``send_mail`` / ``send_image``)."""
    from domovoi.sdk import friend_houses as sdk

    friend_id = await friend_for_house_id(slug, house_id)
    if friend_id is None:
        raise sdk.NotAllowed("not a friend this plugin may mail")
    return await send(slug, friend_id, payload, ttl_s=ttl_s, replace_key=replace_key, version=version)


#: slug -> its friends_changed call waiting to run (coalesced).
_CHANGED: dict[str, asyncio.Task] = {}
FRIENDS_CHANGED_DELAY_SEC = 0.5


def friends_changed(slugs: list[str] | set[str] | tuple[str, ...] | None = None) -> None:
    """``friends()`` may answer differently for these plugins (None: all
    of them): their ``on_friends_changed`` handlers run once shortly after,
    however many changes came together. Never raises."""
    from domovoi.sdk.friend_houses import registered_all

    for api in registered_all():
        if not api.friends_handlers or (slugs is not None and api.slug not in slugs):
            continue
        pending = _CHANGED.get(api.slug)
        if pending is not None and not pending.done():
            continue
        task = _track(_run_friends_changed(api.slug), "friend-houses-friends-changed")
        if task is not None:
            _CHANGED[api.slug] = task


async def _run_friends_changed(slug: str) -> None:
    from domovoi.sdk.friend_houses import HANDLER_DEADLINE_SEC as DEADLINE
    from domovoi.sdk.friend_houses import registered

    await asyncio.sleep(FRIENDS_CHANGED_DELAY_SEC)
    _CHANGED.pop(slug, None)
    api = registered(slug)
    for handler in list(api.friends_handlers if api is not None else []):
        try:
            await asyncio.wait_for(handler(), DEADLINE)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the plugin's own; the next change calls it again
            log.warning("Friend Houses: %s's friends-changed handler failed", slug, exc_info=True)


# ─── receiving: the lane (§6.6) ──────────────────────────────────────────

_LANE: dict[str, Any] = {"queue": None, "workers": []}
_TASKS: set[asyncio.Task] = set()


def _track(coro, name: str) -> asyncio.Task | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    task = loop.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _lane_queue() -> asyncio.Queue:
    q = _LANE["queue"]
    workers = [w for w in _LANE["workers"] if not w.done()]
    if q is None:
        q = _LANE["queue"] = asyncio.Queue(LANE_DEPTH)
    for i in range(LANE_WORKERS - len(workers)):
        workers.append(asyncio.get_running_loop().create_task(_lane_worker(q), name="friend-houses-plugin-lane"))
    _LANE["workers"] = workers
    return q


def on_event(event: dict) -> None:
    """The listener's ``plugin_mail`` event, from service.handle_event: into
    the bounded lane, never the 24-task event pool. A full lane answers
    ``unavailable`` with 10 s."""
    svc = _service()
    msg_id = svc._msg_id_of(event)
    if not msg_id:
        return
    try:
        q = _lane_queue()
    except RuntimeError:
        return
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        _track(svc._answer_listener(f"refused {msg_id} unavailable {LANE_FULL_RETRY_SEC}"),
               "friend-houses-plugin-refuse")


async def _lane_worker(q: asyncio.Queue) -> None:
    svc = _service()
    while True:
        event = await q.get()
        try:
            msg_id = svc._msg_id_of(event)
            answer = await _store_or_unavailable(event)
            if msg_id and answer is not None:
                line = f"stored {msg_id}" if answer == "stored" else \
                    f"refused {msg_id} {answer[0]} {int(answer[1])}"
                await svc._answer_listener(line)
        finally:
            q.task_done()


#: Tries for one mail when the database says try again (a deadlock, a
#: serialization failure): the whole transaction ran back, nothing stored.
STORE_TRIES = 3
_TRANSIENT_SQLSTATES = frozenset({"40P01", "40001"})


def _transient(exc: BaseException) -> bool:
    """A database error that a new try of the same transaction may not
    meet (deadlock detected, serialization failure)."""
    for e in (exc, getattr(exc, "orig", None), getattr(getattr(exc, "orig", None), "__cause__", None)):
        if e is not None and (getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)) in _TRANSIENT_SQLSTATES:
            return True
    return False


async def _store_or_unavailable(event: dict) -> str | tuple[str, int] | None:
    """:func:`store_mail`, tried again at once on a transient database error;
    any failure left is ``unavailable`` (a NACK: the sender keeps the mail
    and resends it, same mail id and seq), never an ACK. A failed try rolled
    back, so the pair's mark didn't move and the resend is stored."""
    for attempt in range(1, STORE_TRIES + 1):
        try:
            return await store_mail(event)
        except Exception as exc:  # noqa: BLE001 — answered unavailable: the sender keeps it
            if attempt < STORE_TRIES and _transient(exc):
                log.info("Friend Houses: storing a plugin mail met %s; trying again",
                         type(getattr(exc, "orig", exc)).__name__)
                continue
            log.exception("Friend Houses: storing a plugin mail failed")
            return ("unavailable", LANE_FULL_RETRY_SEC)
    return ("unavailable", LANE_FULL_RETRY_SEC)


def _hex(event: dict, key: str) -> bytes:
    return bytes.fromhex(str(event.get(key, "")))


async def note_epoch(s, friend: dict[str, Any], epoch: bytes) -> bool:
    """The friend presented ``epoch`` (a hello, a CAPS, a mail). False for a
    retired one (never accepted again, L3). A new epoch retires the old one
    (at most 16 kept), starts every pair's mark at 0 and makes the friend
    due a probe; its CAPS no longer counts (``peer_caps_epoch``)."""
    if len(epoch) != fid.EPOCH_BYTES or epoch in friend["peer_epochs_retired"]:
        return False
    old = friend["peer_epoch"]
    if old == epoch:
        return True
    retired = friend["peer_epochs_retired"] + ([old] if old is not None else [])
    retired = retired[-RETIRED_MAX:]
    # The caller holds the friend row (level 1); its pairs next, in order.
    await lock_pairs(s, "friend_id = :i", {"i": friend["id"]})
    await s.execute(text(
        "UPDATE friend_houses_friends SET peer_epoch = :e, peer_epochs_retired = :r, probe_at = NULL "
        "WHERE id = :i"), {"e": epoch, "r": retired, "i": friend["id"]})
    await s.execute(text(
        "UPDATE friend_houses_plugin_pairs SET in_epoch = :e, in_mark = 0 WHERE friend_id = :i"),
        {"e": epoch, "i": friend["id"]})
    friend["peer_epoch"], friend["peer_epochs_retired"] = epoch, retired
    if old is not None:
        log.info("Friend Houses: a friend house started a new sending epoch")
    return True


async def store_mail(event: dict) -> str | tuple[str, int] | None:
    """Re-check and store one plugin mail. ``"stored"`` (ACK: committed, or
    a duplicate), ``(reason, retry_after)`` for a NACK, or None (no answer:
    an event that failed its checks)."""
    keys = fid.load()
    if keys is None:
        return ("unavailable", NOT_LOADED_RETRY_SEC)
    try:
        house, hello_epoch = _hex(event, "house"), _hex(event, "hello_epoch")
        env = fe.parse(_hex(event, "envelope"), sender=house, recipient=keys.house_key)
        body = env.plugin
    except (ValueError, TypeError, fe.BadEnvelope):
        log.warning("Friend Houses: a plugin mail event failed its checks and was dropped")
        return None
    if env.kind != fe.KIND_PLUGIN:
        return ("not_allowed", 0)
    if env.epoch != hello_epoch:
        return ("not_allowed", 0)
    state = await plugin_state(body.slug)
    async with _db() as s:
        friend = await friend_row(s, house=house, lock=True)
        if friend is None or friend["state"] != "active" or not friend["apps_on"]:
            return ("not_allowed", 0)
        if not await note_epoch(s, friend, env.epoch):
            return ("not_allowed", 0)
        pair = await pair_row(s, friend["id"], body.slug, lock=True)
        if pair is None or not pair["allowed"] or state == "off":
            return ("not_allowed", 0)
        if pair["in_epoch"] != env.epoch:
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET in_epoch = :e, in_mark = 0 "
                "WHERE friend_id = :f AND slug = :s"), {"e": env.epoch, "f": friend["id"], "s": body.slug})
            pair["in_mark"] = 0
        if env.seq <= int(pair["in_mark"]):
            return "stored"  # a lost-ACK resend or a replay: ACK and drop
        if state != "ready":
            return ("unavailable", NOT_LOADED_RETRY_SEC)  # L12: not loaded yet
        left = _paused(pair, _now())
        if left:
            return ("unavailable", int(left) + 1)
        if not await quota_ok(s, "inbox", friend["id"], body.slug, len(body.payload)):
            return ("full", FULL_RETRY_SEC)
        await s.execute(text(
            "INSERT INTO friend_houses_plugin_inbox (friend_id, slug, generation, mail_id, epoch, seq, "
            "payload, size, expires_at, kind) VALUES (:f, :s, :g, :m, :e, :q, :p, :n, "
            "now() + make_interval(secs => CAST(:t AS integer)), :v) "
            "ON CONFLICT (friend_id, slug, mail_id) DO NOTHING"),
            {"f": friend["id"], "s": body.slug, "g": pair["generation"], "m": env.msg_id,
             "e": env.epoch, "q": int(env.seq), "p": body.payload, "n": len(body.payload),
             "t": min(body.ttl_s, TTL_MAX), "v": int(body.version)})
        await s.execute(text(
            "UPDATE friend_houses_plugin_pairs SET in_mark = greatest(in_mark, :q) "
            "WHERE friend_id = :f AND slug = :s"), {"q": int(env.seq), "f": friend["id"], "s": body.slug})
    wake_dispatch()
    return "stored"


# ─── CAPS and the friend's state (§6.8) ──────────────────────────────────

_SPAWNED: set[asyncio.Task] = set()


def _spawn_small(coro, name: str) -> None:
    if len(_SPAWNED) >= SPAWN_MAX:
        coro.close()
        return
    task = _track(coro, name)
    if task is not None:
        _SPAWNED.add(task)
        task.add_done_callback(_SPAWNED.discard)


def on_connected(house: bytes, epoch: bytes) -> None:
    """A friend connected with this hello epoch (the listener's event)."""
    _spawn_small(_note_connected(house, epoch), "friend-houses-plugin-epoch")


async def _note_connected(house: bytes, epoch: bytes) -> None:
    try:
        async with _db() as s:
            friend = await friend_row(s, house=house, lock=True)
            if friend is not None and friend["state"] == "active":
                await note_epoch(s, friend, epoch)
    except Exception:  # noqa: BLE001 — seen again on its next connection
        log.exception("Friend Houses: noting a friend's epoch failed")


def on_caps_event(house: bytes, proto: int, slugs: tuple[str, ...], epoch: bytes) -> None:
    _spawn_small(on_caps(house, proto, slugs, epoch), "friend-houses-plugin-caps")


async def on_caps(house: bytes, proto: int, slugs: tuple[str, ...], epoch: bytes) -> None:
    """A friend's CAPS (checked by the listener and again by service): it
    sets ``peer_proto`` and ``peer_caps`` (L8). A slug in it clears that
    pair's ``not_allowed`` hold and its pair backoff (L11); a slug missing
    from it, for a pair under a hold, deletes the pair's queued mail (M2)."""
    wake_friend = None
    async with _db() as s:
        friend = await friend_row(s, house=house, lock=True)
        if friend is None or friend["state"] != "active":
            return
        if not await note_epoch(s, friend, epoch):
            return
        await s.execute(text(
            "UPDATE friend_houses_friends SET peer_proto = :p, peer_caps = :c, peer_caps_at = now(), "
            "peer_caps_epoch = :e, probe_closes = 0, first_close_at = NULL WHERE id = :i"),
            {"p": int(proto), "c": list(slugs), "e": epoch, "i": friend["id"]})
        await lock_pairs(s, "friend_id = :i", {"i": friend["id"]})  # level 2, in order
        await s.execute(text(
            "UPDATE friend_houses_plugin_pairs SET na_hold_until = NULL, na_first_at = NULL, "
            "pair_retry_at = NULL, pair_backoff_sec = 0 WHERE friend_id = :i AND slug = ANY(:c)"),
            {"i": friend["id"], "c": list(slugs)})
        held = [r[0] for r in await s.execute(text(
            "SELECT slug FROM friend_houses_plugin_pairs WHERE friend_id = :i AND na_first_at IS NOT NULL "
            "AND NOT (slug = ANY(:c))"), {"i": friend["id"], "c": list(slugs)})]
        for slug in held:
            await s.execute(text(
                "DELETE FROM friend_houses_plugin_outbox WHERE friend_id = :i AND slug = :s"),
                {"i": friend["id"], "s": slug})
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET na_hold_until = NULL, na_first_at = NULL "
                "WHERE friend_id = :i AND slug = :s"), {"i": friend["id"], "s": slug})
        wake_friend = friend["id"]
    if wake_friend:
        from domovoi.friend_houses import plugin_sender

        plugin_sender.wake(wake_friend)
        friends_changed()  # has_plugin may read differently


async def caps_slugs(s, friend: dict[str, Any], permitted: list[str] | None = None) -> list[str]:
    """What this house tells ``friend`` in its CAPS, and what the listener
    lets it mail: its allowed pairs whose plugin is enabled and holds the
    permission; nothing while "Apps with this friend" is off."""
    if not friend["apps_on"] or friend["state"] != "active":
        return []
    permitted = await permitted_slugs() if permitted is None else permitted
    rows = await s.execute(text(
        "SELECT slug FROM friend_houses_plugin_pairs WHERE friend_id = :i AND allowed ORDER BY slug"),
        {"i": friend["id"]})
    return [r[0] for r in rows if r[0] in permitted][:32]


async def listener_apps(s) -> dict[str, list[str]]:
    """friend id -> its ``apps`` in friends.json (read by the single
    listener-file writer, under its lock)."""
    permitted = await permitted_slugs()
    rows = await s.execute(text(
        "SELECT p.friend_id, p.slug FROM friend_houses_plugin_pairs p JOIN friend_houses_friends f "
        "ON f.id = p.friend_id WHERE p.allowed AND f.apps_on AND f.state = 'active' "
        "ORDER BY p.friend_id, p.slug"))
    out: dict[str, list[str]] = {}
    for fid_, slug in rows:
        if slug in permitted and len(out.setdefault(fid_, [])) < 32:
            out[fid_].append(slug)
    return out


async def set_caps_needed(s, friend_ids: list[str] | None = None, *, all_apps_on: bool = False) -> None:
    """Mark friends due a CAPS. Their rows are locked in ``id`` order first
    (level 1), so the caller must not hold a pair, sender, state or mail
    row unless it locked these friends before them."""
    if all_apps_on:
        ids = await lock_friends(s, "apps_on")
    elif friend_ids:
        ids = await lock_friends(s, "id = ANY(:i)", {"i": list(friend_ids)})
    else:
        return
    if ids:
        await s.execute(text("UPDATE friend_houses_friends SET caps_needed = TRUE WHERE id = ANY(:i)"),
                        {"i": ids})


# ─── closure (§6.9) ──────────────────────────────────────────────────────


async def close_pairs(s, *, friend_id: str | None = None, slug: str | None = None,
                      revoke: bool = True) -> list[tuple[str, str]]:
    """In the caller's transaction: for each affected pair, delete its
    outbox rows (queued and in flight) and its inbox rows (any state), set
    ``allowed = false`` (unless ``revoke`` is False), bump ``generation``
    and set the friend's ``caps_needed``. The pair rows stay."""
    where, params = [], {"r": revoke}
    if friend_id is not None:
        where.append("friend_id = :f")
        params["f"] = friend_id
    if slug is not None:
        where.append("slug = :s")
        params["s"] = slug
    cond = " AND ".join(where) or "TRUE"
    # Lock order: the friends, then their pairs, then the mail rows.
    await lock_friends(s, f"id IN (SELECT friend_id FROM friend_houses_plugin_pairs WHERE {cond})", params)
    await lock_pairs(s, cond, params)
    pairs = [(r[0], r[1]) for r in await s.execute(text(
        f"UPDATE friend_houses_plugin_pairs SET generation = generation + 1, "
        f"allowed = CASE WHEN :r THEN FALSE ELSE allowed END, na_hold_until = NULL, na_first_at = NULL "
        f"WHERE {cond} RETURNING friend_id, slug"), params)]
    for table in ("friend_houses_plugin_outbox", "friend_houses_plugin_inbox"):
        await s.execute(text(f"DELETE FROM {table} WHERE {cond}"),
                        {k: v for k, v in params.items() if k != "r"})
    await set_caps_needed(s, sorted({f for f, _ in pairs}))
    return pairs


async def after_close(pairs: list[tuple[str, str]], *, write_files: bool = True) -> None:
    """After the closure committed: each friend's sender closes its
    plugin-lane connection, a running handler for a pair is cancelled
    (weight 0, result ignored), then the listener files are written."""
    from domovoi.friend_houses import plugin_sender

    for friend_id in sorted({f for f, _ in pairs}):
        plugin_sender.close(friend_id)
    for key in pairs:
        cancel_running(key)
    if write_files:
        try:
            await _service().write_listener_files()
        except Exception:  # noqa: BLE001 — the reconcile writes them again
            log.exception("Friend Houses: writing the listener files after a closure failed")
    for friend_id in sorted({f for f, _ in pairs}):
        plugin_sender.wake(friend_id)  # the CAPS that says so
    friends_changed({slug for _, slug in pairs})


# ─── the toggle and the pairs (§6.11) ────────────────────────────────────


async def apps_view(friend_id: str) -> dict[str, Any] | None:
    """The admin's view of one friend's apps: the toggle, each plugin with
    the permission (allowed, both houses have it, paused and why), and the
    notices."""
    permitted = await permitted_slugs()
    now = _now()
    async with _db() as s:
        friend = await friend_row(s, friend_id=friend_id)
        if friend is None or friend["state"] != "active":
            return None
        pairs = [_pair(r) for r in await s.execute(text(
            f"SELECT {_PAIR_COLS} FROM friend_houses_plugin_pairs WHERE friend_id = :i"),
            {"i": friend_id})]
        rows = {p["slug"]: p for p in pairs}
    names = await plugin_names()
    apps = []
    for slug in sorted(set(permitted) | {k for k, p in rows.items() if p["allowed"]}):
        p = rows.get(slug)
        left = _paused(p, now) if p else 0
        apps.append({"slug": slug, "name": names.get(slug, slug),
                     "allowed": bool(p and p["allowed"]) and slug in permitted,
                     "needs_confirm": bool(friend["apps_on"] and p is not None and not p["allowed"]
                                           and p["confirmed_at"] is None and slug in permitted),
                     "both_have_it": has_plugin(friend, slug, now),
                     "paused": bool(left), "paused_for_sec": int(left),
                     "pause_cause": p["pause_cause"] if left else None})
    return {"friend_id": friend_id, "apps_on": bool(friend["apps_on"]), "apps": apps,
            "notices": [n for n in await notices() if n["friend_id"] == friend_id]}


async def set_apps(friend_id: str, on: bool, slugs: list[str] | None = None, *,
                   by: str = "admin") -> dict[str, Any] | None:
    """Turn "Apps with this friend" on (with the plugins the admin ticked)
    or off. On: ``apps_on``, the pairs, the listener files, THEN
    ``caps_needed`` (M2: a CAPS that adds a slug goes only after the file
    that lists it). Off: one closure for all its pairs; one empty CAPS."""
    permitted = await permitted_slugs()
    if on:
        wanted = sorted(set(slugs if slugs is not None else permitted) & set(permitted))
        async with _db() as s:
            friend = await friend_row(s, friend_id=friend_id, lock=True)
            if friend is None or friend["state"] != "active":
                return None
            await s.execute(text(
                "UPDATE friend_houses_friends SET apps_on = TRUE, apps_on_at = now(), apps_on_by = :b "
                "WHERE id = :i"), {"b": by[:60], "i": friend_id})
            for slug in permitted:
                await _ensure_pair(s, friend_id, slug)
                await s.execute(text(
                    "UPDATE friend_houses_plugin_pairs SET allowed = :a, confirmed_at = now(), "
                    "confirmed_by = :b WHERE friend_id = :i AND slug = :s"),
                    {"a": slug in wanted, "b": by[:60], "i": friend_id, "s": slug})
        log.warning("Friend Houses: apps with a friend house turned on (%s)", ", ".join(wanted) or "none")
        await _service().write_listener_files()
        async with _db() as s:
            await set_caps_needed(s, [friend_id])
        _refresh_apps_check()
        from domovoi.friend_houses import plugin_sender

        plugin_sender.wake(friend_id)
        friends_changed(wanted)
    else:
        async with _db() as s:
            friend = await friend_row(s, friend_id=friend_id, lock=True)
            if friend is None or friend["state"] != "active":
                return None
            await s.execute(text(
                "UPDATE friend_houses_friends SET apps_on = FALSE WHERE id = :i"), {"i": friend_id})
            pairs = await close_pairs(s, friend_id=friend_id)
            await set_caps_needed(s, [friend_id])
        log.warning("Friend Houses: apps with a friend house turned off")
        await after_close(pairs)
        _refresh_apps_check()
    return await apps_view(friend_id)


async def set_pair(friend_id: str, slug: str, allowed: bool, *, by: str = "admin") -> dict[str, Any] | None:
    """Allow or revoke one plugin with one friend (confirming a re-confirm
    prompt is an allow)."""
    if not fe.valid_slug(slug):
        return None
    if allowed:
        if slug not in await permitted_slugs():
            raise ValueError("That app can't use apps with friends.")
        async with _db() as s:
            friend = await friend_row(s, friend_id=friend_id, lock=True)
            if friend is None or friend["state"] != "active":
                return None
            if not friend["apps_on"]:
                raise ValueError("Turn on apps with this friend first.")
            await _ensure_pair(s, friend_id, slug)
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET allowed = TRUE, confirmed_at = now(), "
                "confirmed_by = :b WHERE friend_id = :i AND slug = :s"),
                {"b": by[:60], "i": friend_id, "s": slug})
        log.warning("Friend Houses: %s allowed with a friend house", slug)
        await _service().write_listener_files()
        async with _db() as s:
            await set_caps_needed(s, [friend_id])
        _refresh_apps_check()
        from domovoi.friend_houses import plugin_sender

        plugin_sender.wake(friend_id)
        friends_changed([slug])
    else:
        async with _db() as s:
            friend = await friend_row(s, friend_id=friend_id, lock=True)  # level 1 before the pair
            if friend is None or friend["state"] != "active":
                return None
            await _ensure_pair(s, friend_id, slug)
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET confirmed_at = coalesce(confirmed_at, now()), "
                "confirmed_by = :b WHERE friend_id = :i AND slug = :s"),
                {"b": by[:60], "i": friend_id, "s": slug})
            pairs = await close_pairs(s, friend_id=friend_id, slug=slug)
        log.warning("Friend Houses: %s revoked with a friend house", slug)
        await after_close(pairs)
        _refresh_apps_check()
    return await apps_view(friend_id)


async def resume(friend_id: str, slug: str) -> dict[str, Any] | None:
    """An admin's Resume: the pause ends, its length starts over, and the
    friend is told again (``caps_needed``), so its sender doesn't sit out a
    pause that has ended (L11)."""
    async with _db() as s:
        # set_caps_needed updates the friend row: lock it before the pair.
        await friend_row(s, friend_id=friend_id, lock=True)
        pair = await pair_row(s, friend_id, slug, lock=True)
        if pair is None:
            return None
        b = breaker_of(pair)
        b.resume()
        await _save_breaker(s, friend_id, slug, b)
        await set_caps_needed(s, [friend_id])
    pool = _iso().POOLS.get(slug)
    if pool is not None:
        pool.resume(friend_id)
    log.warning("Friend Houses: %s mail from a friend house resumed by an admin", slug)
    from domovoi.friend_houses import plugin_sender

    plugin_sender.wake(friend_id)
    wake_dispatch()
    return await apps_view(friend_id)


async def notices() -> list[dict[str, Any]]:
    """The admin's notices (Friend Houses page and Home; logged, never
    spoken): paused pairs (with the four causes), plugins waiting for a
    re-confirm, and a Docker problem."""
    permitted = await permitted_slugs()
    out: list[dict[str, Any]] = []
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT p.friend_id, f.local_name, p.slug, p.paused_until, p.pause_cause, p.allowed, "
            "p.confirmed_at, f.apps_on FROM friend_houses_plugin_pairs p JOIN friend_houses_friends f "
            "ON f.id = p.friend_id WHERE f.state = 'active' ORDER BY p.slug, f.local_name"))).all()
    now = _now()
    names = await plugin_names()
    paused_by_slug: dict[str, int] = {}
    for fid_, name, slug, until, cause, allowed, confirmed, apps_on in rows:
        left = (until.timestamp() - now) if until else 0
        app = names.get(slug, slug)
        name = name or "A friend house"
        if left > 0 and cause:
            paused_by_slug[slug] = paused_by_slug.get(slug, 0) + 1
            out.append({"kind": "paused", "friend_id": fid_, "friend": name, "slug": slug, "app": app,
                        "cause": cause, "paused_for_sec": int(left),
                        "text": pause_text(name, app, cause, int(left))})
        elif apps_on and not allowed and confirmed is None and slug in permitted:
            out.append({"kind": "confirm", "friend_id": fid_, "friend": name, "slug": slug, "app": app,
                        "text": f"{app} can use Apps with friends. Allow it with {name}?"})
    for slug, n in paused_by_slug.items():
        if n >= 2:
            app = names.get(slug, slug)
            out.append({"kind": "plugin", "friend_id": None, "slug": slug, "app": app,
                        "text": f"{app} has failed on mail from {n} friends; it may need an update."})
    dn = _iso().DOCKER_NOTICE
    if dn.get("at") and now - float(dn["at"]) < 24 * 3600:
        out.append({"kind": "docker", "friend_id": None, "slug": None, "app": None,
                    "text": str(dn["why"])})
    return out


def pause_text(friend: str, app: str, cause: str, secs: int) -> str:
    """The admin's notice for a pause (§6.7), naming the friend and the
    plugin (``app``: its display name)."""
    span = "1 hour" if secs <= 3600 else f"{(secs + 3599) // 3600} hours"
    if cause == "restart":
        return f"Mail from {friend} to {app} is paused: a mail from {friend} made Domovoi restart " \
               f"while {app} handled it."
    if cause == "versions":
        return f"{app} at this house and {friend} may be different versions: paused for 1 hour."
    return f"Mail from {friend} to {app} is paused for {span}: it kept failing. " \
           "Other friends aren't affected."


def _on_pause(slug: str, friend_id: str, pause_sec: int, cause: str, after_restart: bool) -> None:
    log.warning("Friend Houses: %s mail from a friend house paused for %d min (%s%s)", slug,
                pause_sec // 60, cause, ", after a restart" if after_restart else "")


# ─── plugin lifecycle (§6.9, §6.11, L11, L12) ────────────────────────────


async def on_plugin_off(slug: str) -> None:
    """Disabled, uninstalled, or upgraded without the permission: one
    closure for every pair of the plugin."""
    if not fe.valid_slug(slug):
        return
    try:
        async with _db() as s:
            pairs = await close_pairs(s, slug=slug)
    except Exception:  # noqa: BLE001 — never fails the lifecycle step
        log.exception("Friend Houses: closing %s's apps with friends failed", slug)
        return
    await after_close(pairs)
    _refresh_apps_check()


async def on_plugin_new(slug: str) -> None:
    """Installed with the permission, or upgraded to gain it: disallowed for
    every friend with apps on until an admin confirms it (re-confirm)."""
    if not fe.valid_slug(slug):
        return
    try:
        async with _db() as s:
            # Lock order: every friend touched below, then the pairs.
            await lock_friends(s, "apps_on OR id IN (SELECT friend_id FROM friend_houses_plugin_pairs "
                                  "WHERE slug = :s)", {"s": slug})
            await s.execute(text(
                "INSERT INTO friend_houses_plugin_pairs (friend_id, slug) SELECT id, :s "
                "FROM friend_houses_friends WHERE apps_on AND state = 'active' "
                "ON CONFLICT (friend_id, slug) DO NOTHING"), {"s": slug})
            await lock_pairs(s, "slug = :s", {"s": slug})
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET allowed = FALSE, confirmed_at = NULL, "
                "confirmed_by = NULL WHERE slug = :s"), {"s": slug})
            await set_caps_needed(s, all_apps_on=True)   # §6.8: an install sets it
    except Exception:  # noqa: BLE001
        log.exception("Friend Houses: preparing %s's re-confirm failed", slug)
        return
    log.warning("Friend Houses: %s can use apps with friends; it waits for an admin to allow it", slug)


async def on_plugin_upgraded(slug: str) -> None:
    """Upgraded, permission kept: no closure. Its ``handler`` and
    ``restart`` pauses end and their length starts over, and its friends
    are told again (L11)."""
    try:
        async with _db() as s:
            # Lock order: the friends set_caps_needed marks, then the pairs.
            await lock_friends(s, "id IN (SELECT friend_id FROM friend_houses_plugin_pairs WHERE slug = :s)",
                               {"s": slug})
            await lock_pairs(s, "slug = :s", {"s": slug})
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET paused_until = CASE WHEN pause_cause IN "
                "('handler', 'restart') THEN NULL ELSE paused_until END, pause_cause = CASE WHEN "
                "pause_cause IN ('handler', 'restart') THEN NULL ELSE pause_cause END, "
                "next_pause_sec = 3600, fault_events = '[]' WHERE slug = :s"), {"s": slug})
            friends = [r[0] for r in await s.execute(text(
                "SELECT friend_id FROM friend_houses_plugin_pairs WHERE slug = :s AND allowed"), {"s": slug})]
            await set_caps_needed(s, friends)
    except Exception:  # noqa: BLE001
        log.exception("Friend Houses: noting %s's upgrade failed", slug)


async def on_plugin_on(slug: str) -> None:
    """Enabled: its allowed friends are told again, after the files."""
    try:
        await _service().write_listener_files()
        async with _db() as s:
            friends = [r[0] for r in await s.execute(text(
                "SELECT friend_id FROM friend_houses_plugin_pairs WHERE slug = :s AND allowed"), {"s": slug})]
            await set_caps_needed(s, friends)
    except Exception:  # noqa: BLE001
        log.exception("Friend Houses: noting %s's enable failed", slug)
    _refresh_apps_check()
    friends_changed([slug])


async def apps_check(slug: str) -> bool:
    """For the decoder's build gate: any friend with apps on and this
    plugin allowed."""
    async with _db() as s:
        row = (await s.execute(text(
            "SELECT 1 FROM friend_houses_plugin_pairs p JOIN friend_houses_friends f ON f.id = p.friend_id "
            "WHERE p.slug = :s AND p.allowed AND f.apps_on AND f.state = 'active' LIMIT 1"),
            {"s": slug})).first()
    return row is not None


def _refresh_apps_check() -> None:
    _iso().FRIEND_APPS_CHECK = apps_check


# ─── the dispatcher: decode, then the handler (§6.7) ─────────────────────

_RUN: dict[str, Any] = {"dispatch": None, "sweep": None, "wake": None, "started": False}
#: (friend id, slug) -> (task, row id, attempt) of what is being worked on
_RUNNING: dict[tuple[str, str], asyncio.Task] = {}
_HANDLERS: dict[tuple[str, str], tuple[asyncio.Task, int, str]] = {}
_CLOSED: set[tuple[str, str]] = set()


def wake_dispatch() -> None:
    ev = _RUN.get("wake")
    if ev is not None:
        ev.set()


def cancel_running(key: tuple[str, str]) -> None:
    """A closure: the pair's running handler is cancelled, weight 0, and its
    result ignored (its row is gone)."""
    h = _HANDLERS.get(key)
    if h is not None:
        _CLOSED.add(key)
        h[0].cancel()


async def dispatch_once(now: float | None = None) -> int:
    """Start work on what may run now: at most 1 item per (plugin,
    friend), 2 decodes per friend across plugins, 4 per plugin, served
    round-robin by friend (L6). Returns how many started."""
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT i.id, i.friend_id, i.slug, i.state FROM friend_houses_plugin_inbox i "
            "JOIN friend_houses_plugin_pairs p ON p.friend_id = i.friend_id AND p.slug = i.slug "
            "JOIN friend_houses_friends f ON f.id = i.friend_id "
            "WHERE i.state IN ('stored', 'decoded') AND (i.next_try_at IS NULL OR i.next_try_at <= now()) "
            "AND i.expires_at > now() AND i.generation = p.generation AND p.allowed AND f.apps_on "
            "AND (p.paused_until IS NULL OR p.paused_until <= now()) ORDER BY i.id LIMIT 500"))).all()
    by_friend: dict[str, list[tuple[int, str, str]]] = {}
    for row_id, friend_id, slug, state in rows:
        by_friend.setdefault(friend_id, []).append((int(row_id), slug, state))
    per_plugin: dict[str, int] = {}
    decoding: dict[str, int] = {}
    for (f, slug), task in _RUNNING.items():
        if not task.done():
            per_plugin[slug] = per_plugin.get(slug, 0) + 1
            decoding[f] = decoding.get(f, 0) + 1
    started = 0
    progress = True
    while progress:
        progress = False
        for friend_id in sorted(by_friend):
            queue = by_friend[friend_id]
            while queue:
                row_id, slug, state = queue[0]
                key = (friend_id, slug)
                busy = key in _RUNNING and not _RUNNING[key].done()
                if busy or per_plugin.get(slug, 0) >= PER_PLUGIN_AT_ONCE or \
                        (state == "stored" and decoding.get(friend_id, 0) >= 2):
                    queue.pop(0)
                    continue
                queue.pop(0)
                task = _track(_work(row_id, friend_id, slug), "friend-houses-plugin-work")
                if task is None:
                    return started
                _RUNNING[key] = task
                per_plugin[slug] = per_plugin.get(slug, 0) + 1
                decoding[friend_id] = decoding.get(friend_id, 0) + 1
                started += 1
                progress = True
                break  # round-robin: next friend
    return started


async def _wait_row(row_id: int, why: str, secs: float = WAIT_RETRY_SEC) -> None:
    async with _db() as s:
        await s.execute(text(
            "UPDATE friend_houses_plugin_inbox SET next_try_at = now() + make_interval(secs => CAST(:t AS integer)) "
            "WHERE id = :i"), {"t": int(secs), "i": row_id})
    log.debug("Friend Houses: a plugin mail waits: %s", why)


async def _quarantine(row_id: int, slug: str, why: str) -> None:
    async with _db() as s:
        await s.execute(text("DELETE FROM friend_houses_plugin_inbox WHERE id = :i"), {"i": row_id})
    log.warning("Friend Houses: a mail from a friend house to %s was quarantined (deleted): %s", slug, why[:120])


async def _fault_row(row_id: int, attempt: str | None, back_to: str, slug: str, why: str) -> None:
    """``faults + 1``; back to ``back_to`` after a backoff, or quarantined
    at :data:`ITEM_FAULTS_MAX`."""
    async with _db() as s:
        row = (await s.execute(text(
            "UPDATE friend_houses_plugin_inbox SET faults = faults + 1, state = :st, attempt_id = NULL, "
            "started_at = NULL, next_try_at = now() + make_interval(secs => CAST(:t AS integer)) "
            "WHERE id = :i AND (CAST(:a AS uuid) IS NULL OR attempt_id = CAST(:a AS uuid)) RETURNING faults"),
            {"st": back_to, "t": FAULT_RETRY_SEC, "i": row_id, "a": attempt})).first()
    if row is not None and int(row[0]) >= ITEM_FAULTS_MAX:
        await _quarantine(row_id, slug, why)


async def _work(row_id: int, friend_id: str, slug: str) -> None:
    key = (friend_id, slug)
    try:
        if await _decode_step(row_id, friend_id, slug):
            await _handle_step(row_id, friend_id, slug)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — core-side: weight 0, the row waits
        log.exception("Friend Houses: working on a plugin mail failed")
    finally:
        if _RUNNING.get(key) is asyncio.current_task():
            _RUNNING.pop(key, None)
        wake_dispatch()


async def _decode_step(row_id: int, friend_id: str, slug: str) -> bool:
    """True when the row is (now) ``decoded`` and may go to the handler."""
    from domovoi.sdk.friend_houses import registered

    iso = _iso()
    async with _db() as s:
        row = (await s.execute(text(
            "SELECT state, payload, generation, kind FROM friend_houses_plugin_inbox WHERE id = :i"),
            {"i": row_id})).first()
    if row is None:
        return False
    if row[0] == "decoded":
        return True
    if row[0] != "stored" or row[1] is None:
        return False
    generation = int(row[2])
    image = int(row[3]) == fe.PLUGIN_BODY_IMAGE
    api = registered(slug)
    pool = iso.POOLS.get(slug)
    if await plugin_state(slug) != "ready" or api is None or api.schema is None or pool is None:
        await _wait_row(row_id, "the plugin isn't ready (no handler, no schema or not loaded)")
        return False
    if pool.schema is None:
        pool.set_schema(api.schema)
    tag, image_bytes = "", b""
    if image:
        # D1: an image goes to the core's image helper, never to the
        # plugin's decoder; and only to a plugin that takes images.
        try:
            tag, image_bytes = fe.split_image_payload(bytes(row[1]))
        except fe.BadEnvelope:
            image_bytes = b""
        if not api.images or not image_bytes:
            async with _db() as s:
                await s.execute(text("DELETE FROM friend_houses_plugin_inbox WHERE id = :i"), {"i": row_id})
            await iso.charge(slug, friend_id, str(row_id), iso.W_REFUSAL,
                             "an image this plugin doesn't take", kind=iso.KIND_REFUSAL)
            log.info("Friend Houses: an image from a friend house to %s was refused: the plugin "
                     "takes no images", slug)
            return False
    attempt = str(uuid.uuid4())

    async def on_start() -> None:
        # H1: the attempt is recorded before the request is written.
        async with _db() as s:
            res = await s.execute(text(
                "UPDATE friend_houses_plugin_inbox SET state = 'decoding', attempt_id = CAST(:a AS uuid), "
                "started_at = now() WHERE id = :i AND state = 'stored' AND generation = :g"),
                {"a": attempt, "i": row_id, "g": generation})
            if not res.rowcount:
                raise RuntimeError("the row changed")

    if image:
        result = await pool.decode_image(friend_id, image_bytes, item_key=str(row_id), on_start=on_start)
        if result.ok:
            info = dict((result.value or {}).get("image") or {})
            result = iso.DecodeResult(
                True, value=result.value, image=result.image, elapsed_ms=result.elapsed_ms,
                canonical=json.dumps({"image": {"width": int(info.get("width", 0)),
                                                "height": int(info.get("height", 0)), "tag": tag}},
                                     sort_keys=True, separators=(",", ":")).encode())
    else:
        result = await pool.decode(friend_id, bytes(row[1]), item_key=str(row_id), on_start=on_start)
    if result.ok:
        async with _db() as s:
            # Lock order: the pair (level 2) before its inbox row (level 5).
            await s.execute(text(
                "UPDATE friend_houses_plugin_pairs SET decode_ms_hour = least(decode_ms_hour + :ms, 2000000000) "
                "WHERE friend_id = :f AND slug = :s"), {"ms": int(result.elapsed_ms), "f": friend_id, "s": slug})
            res = await s.execute(text(
                "UPDATE friend_houses_plugin_inbox SET state = 'decoded', canonical = :c, payload = NULL, "
                "image_png = :png, attempt_id = NULL, started_at = NULL, next_try_at = NULL "
                "WHERE id = :i AND attempt_id = CAST(:a AS uuid)"),
                {"c": result.canonical, "png": result.image, "i": row_id, "a": attempt})
        return bool(res.rowcount)
    if result.outcome == "refused":
        # The decoder said no, a schema refusal, or a failed re-check: the
        # pool charged it (refusal / weight 1); the row is deleted.
        async with _db() as s:
            await s.execute(text("DELETE FROM friend_houses_plugin_inbox WHERE id = :i"), {"i": row_id})
        log.info("Friend Houses: a mail from a friend house to %s was refused: %s", slug, result.reason[:120])
        return False
    if result.outcome == "dropped":
        await _quarantine(row_id, slug, result.reason)
        return False
    if result.outcome == "fault":
        await _fault_row(row_id, attempt, "stored", slug, result.reason)
        return False
    # unavailable: weight 0; back to stored if the attempt was recorded.
    async with _db() as s:
        await s.execute(text(
            "UPDATE friend_houses_plugin_inbox SET state = 'stored', attempt_id = NULL, started_at = NULL, "
            "next_try_at = now() + make_interval(secs => CAST(:t AS integer)) WHERE id = :i AND state IN ('stored', 'decoding') "
            "AND (attempt_id IS NULL OR attempt_id = CAST(:a AS uuid))"),
            {"t": WAIT_RETRY_SEC, "i": row_id, "a": attempt})
    return False


async def _handle_step(row_id: int, friend_id: str, slug: str) -> None:
    from domovoi.sdk.friend_houses import registered

    iso = _iso()
    api = registered(slug)
    if await plugin_state(slug) != "ready" or api is None or api.mail_handler is None:
        await _wait_row(row_id, "the plugin's handler isn't registered")
        return
    keys = fid.load()
    attempt = str(uuid.uuid4())
    async with _db() as s:
        friend = await friend_row(s, friend_id=friend_id)
        row = (await s.execute(text(
            "UPDATE friend_houses_plugin_inbox i SET state = 'handling', attempt_id = CAST(:a AS uuid), "
            "started_at = now() FROM friend_houses_plugin_pairs p WHERE i.id = :i AND i.state = 'decoded' "
            "AND p.friend_id = i.friend_id AND p.slug = i.slug AND p.generation = i.generation AND p.allowed "
            "RETURNING i.canonical, i.mail_id, i.seq, i.kind, i.image_png"), {"a": attempt, "i": row_id})).first()
    if row is None or friend is None or keys is None:
        return
    value = delivered_value(bytes(row[0]), int(row[3]), row[4])  # M5: on every delivery
    house_id = keys.plugin_house_id(slug, friend["house_key"])
    key = (friend_id, slug)
    _CLOSED.discard(key)
    task = asyncio.get_running_loop().create_task(
        api.mail_handler(house_id, bytes(row[1]).hex(), int(row[2]), value),
        name=f"friend-houses-plugin-handler-{slug}")
    _HANDLERS[key] = (task, row_id, attempt)
    try:
        done, _ = await asyncio.wait({task}, timeout=HANDLER_DEADLINE_SEC)
    finally:
        if _HANDLERS.get(key, (None,))[0] is task:
            _HANDLERS.pop(key, None)
    if key in _CLOSED:
        _CLOSED.discard(key)
        if not task.done():
            task.cancel()
        return  # a closure: weight 0, result ignored
    if not done:
        # Core stops waiting: a late result is ignored (the attempt no
        # longer matches), the slot is freed, the friend charged.
        task.cancel()
        task.add_done_callback(_swallow)
        await _fault_row(row_id, attempt, "decoded", slug, "the handler took longer than 10 s")
        await iso.charge(slug, friend_id, str(row_id), iso.W_HANDLER, "the handler timed out",
                         kind=iso.KIND_HANDLER)
        return
    if task.cancelled() or task.exception() is not None:
        why = "the handler was cancelled" if task.cancelled() else \
            f"the handler raised {type(task.exception()).__name__}"
        await _fault_row(row_id, attempt, "decoded", slug, why)
        await iso.charge(slug, friend_id, str(row_id), iso.W_HANDLER, why, kind=iso.KIND_HANDLER)
        return
    async with _db() as s:
        await s.execute(text(
            "DELETE FROM friend_houses_plugin_inbox WHERE id = :i AND state = 'handling' "
            "AND attempt_id = CAST(:a AS uuid)"), {"i": row_id, "a": attempt})


def _swallow(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


# ─── the sweep (§6.2, §6.7) ──────────────────────────────────────────────


async def sweep() -> dict[str, int]:
    """Expired rows (every state but ``handling`` in the inbox; every
    state in the outbox), rows of an older generation, and ``handling``
    rows older than 60 s (a handler timeout)."""
    async with _db() as s:
        out = (await s.execute(text(
            "DELETE FROM friend_houses_plugin_outbox o WHERE o.expires_at <= now() OR NOT EXISTS ("
            "SELECT 1 FROM friend_houses_plugin_pairs p WHERE p.friend_id = o.friend_id "
            "AND p.slug = o.slug AND p.generation = o.generation)"))).rowcount
        inb = (await s.execute(text(
            "DELETE FROM friend_houses_plugin_inbox i WHERE i.state <> 'handling' AND (i.expires_at <= now() "
            "OR NOT EXISTS (SELECT 1 FROM friend_houses_plugin_pairs p WHERE p.friend_id = i.friend_id "
            "AND p.slug = i.slug AND p.generation = i.generation))"))).rowcount
        stale = (await s.execute(text(
            "SELECT id, friend_id, slug, CAST(attempt_id AS text) FROM friend_houses_plugin_inbox "
            "WHERE state = 'handling' AND started_at < now() - make_interval(secs => CAST(:t AS integer))"),
            {"t": HANDLING_STALE_SEC})).all()
    iso = _iso()
    for row_id, friend_id, slug, attempt in stale:
        key = (friend_id, slug)
        h = _HANDLERS.get(key)
        if h is not None and h[1] == row_id:
            _HANDLERS.pop(key, None)
            h[0].cancel()
        if _RUNNING.get(key) is not None and not _RUNNING[key].done():
            _RUNNING[key].cancel()
            _RUNNING.pop(key, None)
        await _fault_row(int(row_id), attempt, "decoded", slug, "the handler ran over a minute")
        await iso.charge(slug, friend_id, str(row_id), iso.W_HANDLER, "the handler ran over a minute",
                         kind=iso.KIND_HANDLER)
    if stale:
        wake_dispatch()
    return {"outbox": int(out or 0), "inbox": int(inb or 0), "stale": len(stale)}


_HOUR = {"at": 0.0}


async def _sweep_loop() -> None:
    from domovoi.friend_houses import plugin_sender

    last_caps = 0.0
    while True:
        await asyncio.sleep(SWEEP_EVERY_SEC)
        try:
            await sweep()
            if time.monotonic() - _HOUR["at"] >= 3600:
                _HOUR["at"] = time.monotonic()
                async with _db() as s:
                    keys = await lock_pairs(s, "decode_ms_hour <> 0")  # level 2, in order
                    if keys:
                        await s.execute(text(
                            "UPDATE friend_houses_plugin_pairs SET decode_ms_hour = 0 "
                            "WHERE (friend_id, slug) IN (SELECT * FROM unnest(CAST(:f AS text[]), "
                            "CAST(:s AS text[])))"),
                            {"f": [k[0] for k in keys], "s": [k[1] for k in keys]})
            if time.monotonic() - last_caps >= CAPS_PASS_EVERY_SEC:
                last_caps = time.monotonic()
                await plugin_sender.caps_pass()
        except Exception:  # noqa: BLE001 — next pass
            log.exception("Friend Houses: the plugin mail sweep failed")


async def _dispatch_loop() -> None:
    ev: asyncio.Event = _RUN["wake"]
    while True:
        try:
            await asyncio.wait_for(ev.wait(), 5.0)
        except asyncio.TimeoutError:
            pass
        ev.clear()
        try:
            await dispatch_once()
        except Exception:  # noqa: BLE001 — next pass
            log.exception("Friend Houses: the plugin mail dispatcher failed")
            await asyncio.sleep(5.0)


# ─── start and stop (H1, M1) ─────────────────────────────────────────────


async def recover_after_start() -> dict[str, int]:
    """At core start, before anything is dispatched. A clean shutdown put
    every ``decoding`` and ``handling`` row back, so one found here means
    the core stopped while working on it (H1): ``decoding`` -> ``stored``
    at weight 1, ``handling`` -> ``decoded`` at weight 3 (the pair pauses at
    once, cause ``restart``); both ``faults + 1``, quarantined at 2. An
    ``inflight`` outbox row goes back to ``queued`` (the resend is a
    duplicate the friend ACKs)."""
    iso = _iso()
    async with _db() as s:
        dec = (await s.execute(text(
            "UPDATE friend_houses_plugin_inbox SET state = 'stored', faults = faults + 1, attempt_id = NULL, "
            "started_at = NULL WHERE state = 'decoding' RETURNING id, friend_id, slug, faults"))).all()
        han = (await s.execute(text(
            "UPDATE friend_houses_plugin_inbox SET state = 'decoded', faults = faults + 1, attempt_id = NULL, "
            "started_at = NULL WHERE state = 'handling' RETURNING id, friend_id, slug, faults"))).all()
        await s.execute(text("UPDATE friend_houses_plugin_outbox SET state = 'queued' WHERE state = 'inflight'"))
    for rows, weight, what in ((dec, iso.W_RESTART_DECODING, "decoding"),
                               (han, iso.W_RESTART_HANDLING, "handling")):
        for row_id, friend_id, slug, faults in rows:
            why = f"Domovoi stopped while the mail was {what}"
            await charge_sink(slug, friend_id, str(row_id), weight, why, iso.KIND_RESTART)
            if int(faults) >= ITEM_FAULTS_MAX:
                await _quarantine(int(row_id), slug, why)
    if dec or han:
        log.warning("Friend Houses: %d plugin mail(s) were being worked on when Domovoi stopped; charged",
                    len(dec) + len(han))
    return {"decoding": len(dec), "handling": len(han)}


def install_hooks() -> None:
    iso = _iso()
    iso.CHARGE_SINK = charge_sink
    if _on_pause not in iso.PAUSE_LISTENERS:
        iso.PAUSE_LISTENERS.append(_on_pause)
    _refresh_apps_check()


async def start() -> None:
    """Core start: recover, then run the dispatcher and the sweep. Never
    raises (no database or no tables: tried at the next start)."""
    if _RUN["started"]:
        return
    install_hooks()
    try:
        await recover_after_start()
    except Exception:  # noqa: BLE001 — the core starts anyway
        log.exception("Friend Houses: recovering plugin mail at start failed")
        return
    _RUN["started"] = True
    _RUN["wake"] = asyncio.Event()
    _RUN["dispatch"] = asyncio.get_running_loop().create_task(_dispatch_loop(), name="friend-houses-plugin-dispatch")
    _RUN["sweep"] = asyncio.get_running_loop().create_task(_sweep_loop(), name="friend-houses-plugin-sweep")
    wake_dispatch()


async def shutdown(*, reset: bool | None = None) -> None:
    """A clean stop: nothing is charged. Work in flight is cancelled and
    its rows go back (``decoding`` -> ``stored``, ``handling`` -> ``decoded``).
    The rows are put back when the service ran (or ``reset`` says so)."""
    reset = _RUN["started"] or bool(_RUNNING) or bool(_HANDLERS) if reset is None else reset
    for name in ("dispatch", "sweep"):
        task = _RUN.get(name)
        if task is not None and not task.done():
            task.cancel()
        _RUN[name] = None
    for task in list(_LANE["workers"]):
        task.cancel()
    _LANE["workers"] = []
    _LANE["queue"] = None
    work = [t for t in list(_RUNNING.values()) + [h[0] for h in _HANDLERS.values()] if not t.done()]
    for t in work:
        t.cancel()
    await asyncio.gather(*work, return_exceptions=True)
    _RUNNING.clear()
    _HANDLERS.clear()
    for t in list(_TASKS):
        if not t.done():
            t.cancel()
    await asyncio.gather(*list(_TASKS), return_exceptions=True)
    _CHANGED.clear()
    _RUN["started"] = False
    if not reset:
        return
    try:
        async with _db() as s:
            await s.execute(text(
                "UPDATE friend_houses_plugin_inbox SET state = 'stored', attempt_id = NULL, started_at = NULL "
                "WHERE state = 'decoding'"))
            await s.execute(text(
                "UPDATE friend_houses_plugin_inbox SET state = 'decoded', attempt_id = NULL, started_at = NULL "
                "WHERE state = 'handling'"))
    except Exception:  # noqa: BLE001 — then the next start charges them
        log.exception("Friend Houses: putting plugin mail back at shutdown failed")


__all__ = ["after_close", "apps_view", "charge_sink", "close_pairs", "delivered_value", "friend_for_house_id",
           "friends_changed", "friends_for", "has_plugin", "notices", "plugin_names", "send_to",
           "on_caps", "on_event", "on_plugin_new", "on_plugin_off", "on_plugin_on", "on_plugin_upgraded",
           "plugin_state", "recover_after_start", "resume", "send", "set_apps", "set_pair", "shutdown",
           "start", "store_mail", "sweep", "wrap"]
