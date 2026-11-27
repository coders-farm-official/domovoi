"""Away chat's rows (V087) and the house setting, for both processes.

An enrolment counts (it is a **current** phone) only while all of these
hold, checked in one query (:data:`_CURRENT_SQL`):

* the device still holds the device key it enrolled with
  (``devices.device_key_hash``: V086);
* the device isn't a shared screen (a shared screen is the household's,
  never a member's phone);
* it was made (or made again) after the household token last changed
  (``household_device_tokens.rotated_at``): changing the token is the
  lost-phone step (docs/SECURITY_PRIVACY.md), so it cuts off away chat too,
  and every phone sets it up again at home with the new token (review H2).

Whether the house runs away chat at all is the setting (``away_chat`` in
``household_settings``, on unless an admin turned it off) and the internet
answer (:func:`domovoi.egress.policy` not ``never``): :func:`house_state`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

SETTINGS_KEY = "away_chat"
#: Rows of away_sends kept: chat.send's idempotency window.
SEND_KEEP_HOURS = 24


@dataclass(frozen=True)
class Phone:
    device_id: str
    tor_auth_pub: str
    noise_pub: bytes

    @property
    def file_id(self) -> str:
        """The phone's name in Tor's ``authorized_clients``: 1-32 lower-case
        letters and digits (tor_config's rule), from its device id."""
        return "p" + hashlib.sha256(self.device_id.encode("utf-8")).hexdigest()[:31]


def tor_auth_raw(value: str) -> bytes | None:
    """The 32 bytes of a Tor client-auth public key (52 characters of
    upper-case base32, no padding), or None for any other shape."""
    if not isinstance(value, str) or len(value) != 52 or value != value.upper():
        return None
    try:
        raw = base64.b32decode(value + "====", casefold=False)
    except (ValueError, TypeError):
        return None
    if len(raw) != 32 or base64.b32encode(raw).decode("ascii").rstrip("=") != value:
        return None
    return raw


# ─── the house setting ────────────────────────────────────────────────────


async def setting_on(s: Any) -> bool:
    row = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                           {"k": SETTINGS_KEY})).first()
    if row is None or not isinstance(row[0], dict):
        return True
    return row[0].get("on") is not False


async def set_setting(s: Any, on: bool) -> None:
    if on:
        await s.execute(text("DELETE FROM household_settings WHERE key = :k"), {"k": SETTINGS_KEY})
        return
    await s.execute(
        text("INSERT INTO household_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": SETTINGS_KEY, "v": json.dumps({"on": False})},
    )


async def house_state(s: Any) -> str:
    """``on``, ``away_off`` (an admin turned it off) or ``internet_off``
    (the internet answer is "never": the go-dark gate)."""
    from domovoi import egress

    if not await setting_on(s):
        return "away_off"
    if egress.policy() == "never":
        return "internet_off"
    return "on"


# ─── phones ───────────────────────────────────────────────────────────────

_CURRENT = (
    "d.device_key_hash IS NOT NULL AND d.device_key_hash = a.device_key_hash "
    "AND NOT COALESCE(d.shared_screen, false) "
    "AND a.updated_at > COALESCE((SELECT t.rotated_at FROM household_device_tokens t WHERE t.id = 1), "
    "'-infinity'::timestamptz)"
)
_CURRENT_SQL = f"FROM away_phones a JOIN devices d ON d.device_id = a.device_id WHERE {_CURRENT}"


async def enroll(s: Any, device_id: str, device_key_hash: str, tor_auth_pub: str,
                 noise_pub: bytes) -> None:
    """Enrol a device, or replace both of its keys."""
    await s.execute(
        text(
            "INSERT INTO away_phones (device_id, device_key_hash, tor_auth_pub, noise_pub) "
            "VALUES (:d, :h, :t, :n) "
            "ON CONFLICT (device_id) DO UPDATE SET device_key_hash = EXCLUDED.device_key_hash, "
            "tor_auth_pub = EXCLUDED.tor_auth_pub, noise_pub = EXCLUDED.noise_pub, updated_at = now()"
        ),
        {"d": device_id, "h": device_key_hash, "t": tor_auth_pub, "n": noise_pub},
    )


async def noise_taken(s: Any, noise_pub: bytes, device_id: str) -> bool:
    """Whether another device already enrolled this Noise key."""
    row = (await s.execute(
        text("SELECT 1 FROM away_phones WHERE noise_pub = :n AND device_id <> :d"),
        {"n": noise_pub, "d": device_id},
    )).first()
    return row is not None


async def revoke(s: Any, device_id: str) -> bool:
    """Forget a device's away keys. True when it had any."""
    row = (await s.execute(
        text("DELETE FROM away_phones WHERE device_id = :d RETURNING 1"), {"d": device_id},
    )).first()
    return row is not None


async def is_enrolled(s: Any, device_id: str) -> bool:
    row = (await s.execute(text(f"SELECT 1 {_CURRENT_SQL} AND a.device_id = :d"),
                           {"d": device_id})).first()
    return row is not None


async def current_phones(s: Any) -> list[Phone]:
    rows = (await s.execute(
        text(f"SELECT a.device_id, a.tor_auth_pub, a.noise_pub {_CURRENT_SQL} ORDER BY a.device_id")
    )).all()
    return [Phone(str(r[0]), str(r[1]), bytes(r[2])) for r in rows]


async def phone_for(s: Any, device_id: str, noise_pub: bytes) -> bool:
    """Whether ``device_id`` is a current phone enrolled with exactly
    ``noise_pub``."""
    row = (await s.execute(
        text(f"SELECT 1 {_CURRENT_SQL} AND a.device_id = :d AND a.noise_pub = :n"),
        {"d": device_id, "n": noise_pub},
    )).first()
    return row is not None


async def list_for_admin(s: Any) -> list[dict[str, Any]]:
    """Every enrolment, for System → Chat from away: the device's name and
    platform, when it was set up, and whether it counts now (a device that
    lost its key or became a shared screen doesn't, nor one set up before
    the household token last changed)."""
    rows = (await s.execute(text(
        "SELECT a.device_id, d.name, d.platform, a.created_at, a.updated_at, "
        f"({_CURRENT}) AS current "
        "FROM away_phones a JOIN devices d ON d.device_id = a.device_id "
        "ORDER BY a.created_at"
    ))).mappings().all()
    return [
        {"device_id": r["device_id"], "name": r["name"], "platform": r["platform"],
         "enrolled_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
         "current": bool(r["current"])}
        for r in rows
    ]


# ─── chat.send's idempotency ──────────────────────────────────────────────


async def earlier_send(s: Any, device_id: str, client_id: str) -> tuple[int, int] | None:
    """``(thread_id, message_id)`` of this device's send with this
    ``client_id`` in the last day, or None."""
    row = (await s.execute(
        text("SELECT thread_id, message_id FROM away_sends WHERE device_id = :d "
             "AND client_id = CAST(:c AS uuid) "
             "AND created_at > now() - make_interval(hours => :h)"),
        {"d": device_id, "c": client_id, "h": SEND_KEEP_HOURS},
    )).first()
    return (int(row[0]), int(row[1])) if row else None


async def record_send(s: Any, device_id: str, client_id: str, thread_id: int, message_id: int) -> None:
    await s.execute(
        text("DELETE FROM away_sends WHERE created_at < now() - make_interval(hours => :h)"),
        {"h": SEND_KEEP_HOURS},
    )
    await s.execute(
        text("INSERT INTO away_sends (device_id, client_id, thread_id, message_id) "
             "VALUES (:d, CAST(:c AS uuid), :t, :m) "
             "ON CONFLICT (device_id, client_id) DO UPDATE SET thread_id = EXCLUDED.thread_id, "
             "message_id = EXCLUDED.message_id, created_at = now()"),
        {"d": device_id, "c": client_id, "t": thread_id, "m": message_id},
    )
