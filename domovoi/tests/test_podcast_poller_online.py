"""B4: podcasts are honest about the internet.

* The poller worker ``requires_online`` (the runner skips its ticks while
  the probe says offline), and a tick does nothing when internet access is
  turned off (the web's "poll now" runs ``tick()`` outside that gate).
* A download that fails because the line is down — internet access turned
  off, a refused connection, a name that doesn't resolve while offline —
  puts the episode back to ``pending``; it is never marked ``failed``
  (the durable-failure rule). The pass stops only when the line itself is
  gone (test_internet_review_fixes covers one dead host among good ones). A real failure (an HTTP
  error, a house-local URL, an over-cap body) is still ``failed``.
* Each poll stores the show's artwork on the server (B11).
* The voice "subscribe to X" promises downloads only when the poller is
  on, says so in the internet-off words under ``never``, and makes no
  iTunes request then.
"""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from domovoi import egress, net_safety
from domovoi.config import settings
from domovoi.handlers.spoken_audio import SpokenAudioHandler
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db
from domovoi.workers import podcast_feed_poller as poller

PUBLIC_V4 = "93.184.216.34"
EPISODE = {
    "id": 21, "subscription_id": 1, "guid": "ep-21", "title": "Episode 21",
    "sub_title": "Show", "enclosure_url": "https://feeds.example.com/ep21.mp3",
}


@pytest.fixture
def dns(monkeypatch):
    def fake_resolve(host: str):
        if host.lower() == "feeds.example.com":
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", fake_resolve)
    monkeypatch.setattr(settings, "outbound_allow_hosts", "", raising=False)


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).split()), params))
        return SimpleNamespace(first=lambda: None, rowcount=0)

    async def commit(self) -> None:
        pass

    def final_status(self) -> str | None:
        """The status the last download_status UPDATE wrote."""
        import re

        for sql, _params in reversed(self.calls):
            m = re.search(r"download_status\s*=\s*'(\w+)'", sql)
            if m:
                return m.group(1)
        return None


def _transport(monkeypatch, handler) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_the_poller_requires_online() -> None:
    assert poller.PodcastFeedPoller.requires_online is True


@pytest.mark.asyncio
async def test_a_tick_does_nothing_with_internet_access_turned_off(monkeypatch) -> None:
    def no_db():  # pragma: no cover - reaching the DB IS the failure
        raise AssertionError("polled with internet access turned off")

    monkeypatch.setattr(poller, "session_scope", no_db)
    with egress.override_policy("never"):
        assert await poller.PodcastFeedPoller().tick() == {
            "polled": 0, "new": 0, "downloaded": 0, "evicted": 0,
        }


@pytest.mark.asyncio
async def test_internet_turned_off_mid_download_leaves_the_episode_pending(monkeypatch, tmp_path, dns) -> None:
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))

    async def refused(client, url, **kw):
        raise egress.InternetTurnedOff(url)

    monkeypatch.setattr(net_safety, "open_stream", refused)
    session = _RecordingSession()
    assert await poller.download_episode(session, dict(EPISODE)) is None
    assert session.final_status() == "pending"
    assert not any("'failed'" in sql for sql, _p in session.calls)
    assert list(tmp_path.rglob("*.mp3")) == []


@pytest.mark.asyncio
async def test_a_dropped_connection_leaves_the_episode_pending(monkeypatch, tmp_path, dns) -> None:
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))

    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    _transport(monkeypatch, down)
    session = _RecordingSession()
    assert await poller.download_episode(session, dict(EPISODE)) is None
    assert session.final_status() == "pending"


@pytest.mark.asyncio
async def test_a_name_that_does_not_resolve_offline_leaves_it_pending(monkeypatch, tmp_path, dns) -> None:
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    session = _RecordingSession()
    ep = dict(EPISODE, enclosure_url="https://cdn.unresolvable.example/ep.mp3")
    assert await poller.download_episode(session, ep) is None
    assert session.final_status() == "pending"


@pytest.mark.asyncio
async def test_an_http_error_or_a_house_local_url_is_still_failed(monkeypatch, tmp_path, dns) -> None:
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    _transport(monkeypatch, lambda request: httpx.Response(404))
    session = _RecordingSession()
    assert await poller.download_episode(session, dict(EPISODE)) is False
    assert session.final_status() == "failed"

    session = _RecordingSession()
    ep = dict(EPISODE, enclosure_url="http://127.0.0.1:6370/v1/admin/snapshot")
    assert await poller.download_episode(session, ep) is False
    assert session.final_status() == "failed"


def test_transient_classification() -> None:
    req = httpx.Request("GET", "https://feeds.example.com/x.mp3")
    assert poller.is_transient_fetch_error(egress.InternetTurnedOff("x"))
    assert poller.is_transient_fetch_error(httpx.ConnectError("x", request=req))
    assert poller.is_transient_fetch_error(httpx.ReadTimeout("x", request=req))
    assert poller.is_transient_fetch_error(
        net_safety.UnsafeOutboundURL("https://a.example/x", "host 'a.example' does not resolve")
    )
    assert not poller.is_transient_fetch_error(
        net_safety.UnsafeOutboundURL("http://127.0.0.1/", "host '127.0.0.1' resolves to a non-public address")
    )
    assert not poller.is_transient_fetch_error(net_safety.ResponseTooLarge("https://a.example/x", 10))
    resp = httpx.Response(404, request=req)
    assert not poller.is_transient_fetch_error(httpx.HTTPStatusError("404", request=req, response=resp))


@pytest.mark.asyncio
async def test_a_poll_stores_the_show_artwork_on_the_server(monkeypatch) -> None:
    feed = SimpleNamespace(
        feed={"title": "Show", "image": {"href": "https://art.example.com/feed.jpg"}},
        entries=[],
    )
    monkeypatch.setattr(poller, "_parse_feed_blocking", lambda url: feed)
    stored: list[tuple[int, str]] = []

    async def fake_ensure(sub_id, url):
        stored.append((sub_id, url))

    monkeypatch.setattr(poller.podcast_artwork, "ensure_artwork", fake_ensure)

    class _Session(_RecordingSession):
        async def execute(self, statement, params=None):
            await super().execute(statement, params)
            # RETURNING artwork: the feed's image (or the stored one).
            return SimpleNamespace(first=lambda: ("https://art.example.com/feed.jpg",), rowcount=0)

    session = _Session()
    await poller.poll_subscription(session, {"id": 4, "feed_url": "https://feeds.example.com/s.xml"})
    assert stored == [(4, "https://art.example.com/feed.jpg")]
    assert any("RETURNING artwork" in sql for sql, _ in session.calls)


@requires_db
@pytest.mark.asyncio
async def test_a_pass_stops_downloading_when_the_line_is_down(db_session, monkeypatch) -> None:
    sub = (await db_session.execute(text(
        "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) VALUES ('https://feeds.example.com/s.xml', 'S', 5) RETURNING id"
    ))).scalar_one()
    for i in range(3):
        await db_session.execute(text(
            "INSERT INTO podcast_episodes (subscription_id, guid, title, enclosure_url, download_status) "
            "VALUES (:s, :g, :t, 'https://feeds.example.com/e.mp3', 'pending')"
        ), {"s": sub, "g": f"g{i}", "t": f"e{i}"})
    await db_session.commit()

    async def no_poll(session, sub):
        return 0

    attempts = []

    async def line_down(session, ep):
        attempts.append(ep["id"])
        return None

    class OfflineProbe:
        online = True

        async def check_now(self):
            self.online = False
            return False

    monkeypatch.setattr(poller, "poll_subscription", no_poll)
    monkeypatch.setattr(poller, "download_episode", line_down)
    # After the failure the probe looks again and says the line is down.
    monkeypatch.setattr(poller.connectivity, "current_probe", lambda: OfflineProbe())
    counts = await poller.PodcastFeedPoller().tick()
    assert counts["downloaded"] == 0
    assert len(attempts) == 1                       # the rest wait for the next pass
    statuses = (await db_session.execute(text(
        "SELECT download_status FROM podcast_episodes ORDER BY id"
    ))).scalars().all()
    assert statuses == ["pending", "pending", "pending"]


# ─── The voice "subscribe" ─────────────────────────────────────────────────


@pytest.fixture
def lookup(monkeypatch):
    async def fake_lookup(self, show):
        return "https://feeds.example.com/daily.xml", "The Daily", "https://art.example.com/600.jpg"

    monkeypatch.setattr(SpokenAudioHandler, "_itunes_lookup", fake_lookup)
    scheduled: list[tuple[int, str]] = []
    from domovoi import podcast_artwork

    monkeypatch.setattr(
        podcast_artwork, "schedule_ensure",
        lambda sub_id, url: scheduled.append((sub_id, url)) or True,
    )
    return scheduled


@requires_db
@pytest.mark.asyncio
async def test_subscribe_promises_downloads_only_when_the_poller_is_on(db_session, monkeypatch, lookup) -> None:
    h = SpokenAudioHandler()
    ctx = Context(session_id=None, room_id="kitchen")
    monkeypatch.setattr(settings, "podcast_feed_poller_enabled", True)
    on = await h._subscribe(ctx, db_session, "the daily")
    assert on.text == "Subscribed to The Daily. I'll download new episodes as they come out."
    monkeypatch.setattr(settings, "podcast_feed_poller_enabled", False)
    off = await h._subscribe(ctx, db_session, "the daily")
    assert off.text == (
        "Subscribed to The Daily. Automatic downloads are off, so new episodes "
        "won't download on their own; you can turn them on in Settings."
    )
    await db_session.commit()
    rows = (await db_session.execute(text(
        "SELECT id, artwork FROM podcast_subscriptions"
    ))).all()
    assert len(rows) == 1 and rows[0].artwork == "https://art.example.com/600.jpg"
    # The directory's artwork is fetched by the server, in the background.
    assert lookup[0] == (rows[0].id, "https://art.example.com/600.jpg")


@pytest.mark.asyncio
async def test_subscribe_under_never_says_so_without_a_lookup(monkeypatch) -> None:
    async def no_lookup(self, show):  # pragma: no cover - reaching this IS the failure
        raise AssertionError("asked iTunes with internet access turned off")

    monkeypatch.setattr(SpokenAudioHandler, "_itunes_lookup", no_lookup)
    h = SpokenAudioHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=False)
    with egress.override_policy("never"):
        direct = await h._subscribe(ctx, None, "the daily")
        offline = await h.fallback_offline(
            Intent(transcript="subscribe to the daily", room_id="kitchen"), ctx, None,
        )
    want = "I'm set to stay off the internet, so I can't subscribe to a new podcast."
    assert direct.text == offline.text == want


@pytest.mark.asyncio
async def test_subscribe_offline_keeps_todays_words_when_unanswered() -> None:
    h = SpokenAudioHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=False)
    resp = await h.fallback_offline(Intent(transcript="subscribe to the daily", room_id="kitchen"), ctx, None)
    assert resp.text == "I need an internet connection to subscribe to a new podcast."


@pytest.mark.asyncio
async def test_the_itunes_lookup_goes_through_the_egress_client(monkeypatch) -> None:
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(200, json={"results": [{
            "feedUrl": "https://feeds.example.com/d.xml", "collectionName": "D",
            "artworkUrl600": "https://art.example.com/600.jpg",
        }]})

    _transport(monkeypatch, handler)
    h = SpokenAudioHandler()
    assert await h._itunes_lookup("d") == (
        "https://feeds.example.com/d.xml", "D", "https://art.example.com/600.jpg",
    )
    with egress.override_policy("never"):
        assert await h._itunes_lookup("d") == (None, None, None)
    assert seen == ["itunes.apple.com"]
