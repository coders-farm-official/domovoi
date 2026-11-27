"""Shared pieces of the away chat tests (test_away_enroll.py,
test_away_bridge.py, test_away_integration.py): a claimed house, a phone
that registered and holds its device key, its two away keys, and the
tables cleaned before and after."""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import text

from domovoi import chat_access
from domovoi.db.session import engine
from domovoi.friend_houses.noise import public_bytes
from domovoi.tests.auth_testkit import HEADER, bearer, claim_admin, db_device_token

RW = {"X-Requested-With": "domovoi-tests"}
KEY = chat_access.DEVICE_KEY_HEADER


async def clean_away() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE away_phones, away_sends"))
        await conn.execute(text("DELETE FROM household_settings WHERE key = 'away_chat'"))


@pytest.fixture
async def away_db(_db):  # noqa: F811 — auth_testkit's fixture, imported by the test module
    from web.backend import away_bridge

    await clean_away()
    away_bridge.reset()
    yield
    away_bridge.reset()
    await clean_away()


async def claimed(c) -> tuple[dict[str, str], dict[str, str], str]:
    """(household headers, admin headers, admin token) of a claimed house."""
    token = await claim_admin(c)
    c.cookies.clear()
    base = {**RW, HEADER: await db_device_token()}
    return base, {**base, **bearer(token)}, token


async def phone(c, base: dict[str, str], prefix: str = "android") -> dict[str, Any]:
    """A phone that registered and holds its device key, with fresh away keys."""
    dev = f"{prefix}-{uuid.uuid4().hex[:10]}"
    r = await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True}, headers=base)
    assert r.status_code == 200, r.text
    c.cookies.clear()
    noise = X25519PrivateKey.generate()
    return {
        "id": dev,
        "headers": {**base, KEY: r.json()["device_key"], "X-Domovoi-Device-Id": dev},
        "noise": noise,
        "body": {"tor_auth_pub": tor_auth_pub(), "noise_pub": b64url(public_bytes(noise))},
    }


def tor_auth_pub() -> str:
    return base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def enroll(c, p: dict[str, Any]) -> dict[str, Any]:
    r = await c.post("/api/away/enroll", json=p["body"], headers=p["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def lines(body: bytes) -> list[dict[str, Any]]:
    return [json.loads(x) for x in body.decode("utf-8").splitlines() if x.strip()]
