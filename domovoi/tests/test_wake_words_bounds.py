"""CORE-47 (round 6): several wake words per room, bounded.

The card's proof of concept: ``PUT /v1/admin/wake/room`` with tens of
thousands of unique slugs (an admin Bearer, or any LAN host before
first-run setup) made ``check_word_list`` compare each slug with the list
so far before it said "at most 5": 20,000 took about 2.4 s on the event
loop, with every room's audio stalled. And ``json`` takes ``Infinity``: a
paired room's hello with ``wake_words_max: Infinity`` raised OverflowError
after pairing had passed, ending its own session.

DB-free: the core app over ASGI with the auth primitives faked.
"""

from __future__ import annotations

import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import main, wake_default
from domovoi.tests.auth_testkit import install_fake_db


def _slugs(n: int) -> list[str]:
    return [f"word_{i}" for i in range(n)]


def test_a_long_list_is_refused_before_it_is_walked() -> None:
    started = time.perf_counter()
    for n in (5_000, 10_000, 20_000, 200_000):
        with pytest.raises(ValueError, match="at most 5"):
            wake_default.check_word_list(_slugs(n))
    assert time.perf_counter() - started < 0.5
    assert wake_default.check_word_list(_slugs(5)) == _slugs(5)
    with pytest.raises(ValueError, match="at most 5"):
        wake_default.check_word_list(_slugs(6))


@pytest.mark.asyncio
async def test_the_core_route_refuses_more_than_five_slugs_before_the_handler(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False)   # before setup: the LAN grace
    called = []
    monkeypatch.setattr(wake_default, "check_word_list", lambda raw: called.append(raw) or raw)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://127.0.0.1:6370") as c:
        r = await c.put("/v1/admin/wake/room", json={"room_id": "kitchen", "slugs": _slugs(20_000)})
    assert r.status_code == 422, r.text
    assert called == []


@pytest.mark.parametrize("raw", [float("inf"), float("-inf"), float("nan")])
def test_a_hello_with_a_non_finite_maximum_reads_as_unsaid(raw) -> None:
    assert wake_default.device_max(raw) is None


def test_infinity_straight_from_the_wire() -> None:
    hello = json.loads('{"type": "hello", "wake_words_max": Infinity}')
    assert wake_default.device_max(hello["wake_words_max"]) is None
    assert wake_default.device_max(json.loads("1e308")) == wake_default.MAX_ROOM_WORDS
    assert wake_default.device_max(3) == 3 and wake_default.device_max(3.0) == 3
