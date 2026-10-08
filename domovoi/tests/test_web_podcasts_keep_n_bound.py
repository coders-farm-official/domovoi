"""A subscription's ``keep_n`` is bounded (WEB-13).

``keep_n`` is how many of a show's newest episodes the feed poller keeps
downloaded — each up to 512 MB, and ``enforce_keep_n`` evicts nothing
below it. It was floored at 1 and never capped, so one device-tier
subscribe with ``keep_n: 100000`` marked a show's whole back catalogue
for download. Outside 1–50 is now a 422, refused before anything is
stored.

DB-free: auth faked; the database is a marker that raises, so a 422 also
proves nothing was written.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

import web.backend.api.podcasts as podcasts_api
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.main import app

DEVICE_TOKEN = "d3v1ce-t0ken"


class Stored(BaseException):
    """The subscription reached the database."""


@pytest.fixture
def setup(monkeypatch):
    install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)

    @asynccontextmanager
    async def _db():
        raise Stored("reached the database")
        yield  # pragma: no cover

    monkeypatch.setattr(podcasts_api, "session_scope", _db)


def _subscribe(keep_n):
    c = TestClient(app, headers={"X-Requested-With": "domovoi-tests", HEADER: DEVICE_TOKEN})
    return c.post(
        "/api/podcasts/subscriptions",
        json={"feed_url": "https://feeds.example.com/show.xml", "keep_n": keep_n},
    )


@pytest.mark.parametrize("keep_n", [100000, 51, 0, -5])
def test_an_out_of_range_keep_n_is_refused_before_anything_is_stored(setup, keep_n):
    r = _subscribe(keep_n)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("keep_n", [1, 5, 50])
def test_a_sane_keep_n_is_stored(setup, keep_n):
    with pytest.raises(Stored):
        _subscribe(keep_n)


def test_the_cap_is_the_documented_one():
    assert podcasts_api.KEEP_N_MAX == 50
    assert podcasts_api.SubscribeRequest().keep_n == 5
