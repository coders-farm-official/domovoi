"""Test clients that are a device of their own (private chats, 1.5.0).

Since V086 a home-server chat is private to the device key the server
mints at a device's first registration (domovoi/chat_access.py), and a
caller with no key starts shared chats that only an admin may delete. A
chat test that creates, reads and deletes its own chats therefore talks
as one device: these clients register a fresh device id first, and the
key comes back as the ``domovoi-device-key`` cookie, which the client then
carries on every request (as a browser does).

A fresh id each time: a row that already holds a key (from an earlier
run against the same test database) is never given another.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from domovoi import chat_access

RW = {"X-Requested-With": "domovoi-tests"}


def new_device_id(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class DeviceTestClient(TestClient):
    """A TestClient registered as its own device (``device_id``; its key,
    when the database has V086, in ``device_key`` and the cookie jar)."""

    def __init__(self, app: Any, *, device_id: str | None = None, **kw: Any) -> None:
        kw.setdefault("headers", dict(RW))
        super().__init__(app, **kw)
        self.device_id = device_id or new_device_id()
        r = self.post("/api/devices/register", json={"device_id": self.device_id, "want_device_key": True})
        assert r.status_code == 200, r.text
        self.device_key = r.json().get("device_key")

    def key_headers(self) -> dict[str, str]:
        return {chat_access.DEVICE_KEY_HEADER: self.device_key} if self.device_key else {}


class DeviceAsyncClient(AsyncClient):
    """:class:`DeviceTestClient` for ``async with``."""

    def __init__(self, app: Any, *, device_id: str | None = None, raise_app_exceptions: bool = True,
                 **kw: Any) -> None:
        kw.setdefault("headers", dict(RW))
        kw.setdefault("base_url", "http://test")
        super().__init__(transport=ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions), **kw)
        self.device_id = device_id or new_device_id()
        self.device_key: str | None = None

    async def __aenter__(self) -> "DeviceAsyncClient":
        await super().__aenter__()
        r = await self.post("/api/devices/register", json={"device_id": self.device_id, "want_device_key": True})
        assert r.status_code == 200, r.text
        self.device_key = r.json().get("device_key")
        return self

    def key_headers(self) -> dict[str, str]:
        return {chat_access.DEVICE_KEY_HEADER: self.device_key} if self.device_key else {}
