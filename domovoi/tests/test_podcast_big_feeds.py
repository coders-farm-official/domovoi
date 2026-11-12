"""Big podcast feeds are read, unchanged ones aren't refetched, and a show
that can't be read is named (2026-11-12).

A real daily show's feed was 30 MB. The poller fetched at most 8 MiB and
RAISED past that, ``tick()`` swallowed it as a log line, and "poll now"
answered 200 with nothing polled: the show downloaded nothing and kept its
feed URL as its title. Now:

* ``net_safety.fetch_bytes_sync(..., truncate=True)`` keeps the first
  ``max_bytes`` and stops reading (same URL/redirect checks);
* ``domovoi.feed_prefix.cut_to_last_item`` cuts that prefix after its last
  REAL closing item tag (never one inside CDATA or a comment) and closes
  the containers the document opened, so feedparser reads the newest
  episodes; no whole item -> a clear "feed too large";
* each feed's ETag / Last-Modified is sent back on the next poll, once the
  previous poll was committed, and a 304 is not parsed;
* ``tick()`` returns ``failed`` + ``failures`` (plain words), POST
  /api/podcasts/poll carries them, and the page's toast names the show.

Offline: every fetch goes to an httpx MockTransport.
"""

from __future__ import annotations

import ipaddress
import logging
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from domovoi import feed_prefix, net_safety
from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from domovoi.workers import podcast_feed_poller as poller

PUBLIC_V4 = "93.184.216.34"
FEED_URL = "https://feeds.example.com/big.xml"
_REAL_FETCH = net_safety.fetch_bytes_sync


@pytest.fixture
def dns(monkeypatch):
    def fake_resolve(host: str):
        if host.lower() == "feeds.example.com":
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", fake_resolve)
    monkeypatch.setattr(settings, "outbound_allow_hosts", "", raising=False)


@pytest.fixture(autouse=True)
def _fresh_validators():
    poller.forget_validators()
    yield
    poller.forget_validators()


def _serve(monkeypatch, handler) -> list[httpx.Request]:
    """Route the poller's fetch through the real ``fetch_bytes_sync`` (URL
    check, truncation) onto a MockTransport answering with ``handler``."""
    seen: list[httpx.Request] = []
    real = _REAL_FETCH

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def fetch(url, **kw):
        with httpx.Client(transport=httpx.MockTransport(record)) as client:
            return real(url, client=client, **kw)

    monkeypatch.setattr(poller.net_safety, "fetch_bytes_sync", fetch)
    return seen


# ─── Generated feeds ──────────────────────────────────────────────────────

def _day(i: int) -> str:
    # Episode i was published i days before 2026-11-10 (i=0 is newest).
    from datetime import datetime, timedelta, timezone

    d = datetime(2026, 11, 10, 12, tzinfo=timezone.utc) - timedelta(days=i)
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rss(n: int, *, pad: int = 0, oldest_first: bool = False, cdata_trap: bool = False) -> bytes:
    order = range(n - 1, -1, -1) if oldest_first else range(n)
    items = []
    for i in order:
        notes = "x" * pad
        if cdata_trap:
            notes += "</item></channel></rss> not the end"
        items.append(
            f"<item><title>Episode {i}</title><guid>ep-{i}</guid>"
            f"<pubDate>{_day(i)}</pubDate>"
            f"<description><![CDATA[<p>{notes}</p>]]></description>"
            f'<enclosure url="https://feeds.example.com/ep{i}.mp3" type="audio/mpeg" length="1"/>'
            f"</item>\n"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Big Show</title><itunes:author>Host</itunes:author>\n"
        + "".join(items)
        + "</channel></rss>\n"
    ).encode()


def _atom(n: int, *, pad: int = 0, prefix: str = "") -> bytes:
    p = f"{prefix}:" if prefix else ""
    ns = f'xmlns:{prefix}="http://www.w3.org/2005/Atom"' if prefix else 'xmlns="http://www.w3.org/2005/Atom"'
    entries = "".join(
        f"<{p}entry><{p}id>urn:ep:{i}</{p}id><{p}title>Episode {i}</{p}title>"
        f"<{p}updated>2026-11-{10 - (i % 9):02d}T00:00:00Z</{p}updated>"
        f"<{p}summary>{'y' * pad}</{p}summary>"
        f'<{p}link rel="enclosure" href="https://feeds.example.com/a{i}.mp3"/></{p}entry>\n'
        for i in range(n)
    )
    return (
        f'<?xml version="1.0"?>\n<{p}feed {ns}><{p}title>Atom Show</{p}title>\n'
        + entries + f"</{p}feed>\n"
    ).encode()


def _parse(doc: bytes):
    import feedparser

    return feedparser.parse(doc)


# ─── feed_prefix: the cut ─────────────────────────────────────────────────

def test_an_rss_prefix_is_cut_after_its_last_whole_item_and_closed() -> None:
    whole = _rss(10)
    prefix = whole[: whole.index(b"<item><title>Episode 7") + 30]   # mid item 7
    out = feed_prefix.cut_to_last_item(prefix)
    assert out.rstrip().endswith(b"</item>\n</channel></rss>")
    parsed = _parse(out)
    assert not parsed.bozo, parsed.get("bozo_exception")
    assert parsed.feed.title == "Big Show"
    assert [e.id for e in parsed.entries] == [f"ep-{i}" for i in range(7)]


@pytest.mark.parametrize("prefix", ["", "atom"])
def test_an_atom_prefix_is_cut_after_its_last_whole_entry(prefix) -> None:
    whole = _atom(6, prefix=prefix)
    p = f"{prefix}:" if prefix else ""
    cut_at = whole.index(f"<{p}entry><{p}id>urn:ep:4".encode()) + 20
    out = feed_prefix.cut_to_last_item(whole[:cut_at])
    assert out.rstrip().endswith(f"</{p}feed>".encode())
    parsed = _parse(out)
    assert not parsed.bozo, parsed.get("bozo_exception")
    assert parsed.feed.title == "Atom Show"
    assert [e.id for e in parsed.entries] == [f"urn:ep:{i}" for i in range(4)]


def test_a_closing_tag_inside_cdata_is_not_a_cut_point() -> None:
    """Show notes that QUOTE ``</item></channel></rss>`` in CDATA: a cut
    there would end the document in the middle of an item."""
    whole = _rss(3, cdata_trap=True)
    # The prefix ends inside item 1's CDATA, just past the quoted tags.
    trap = whole.index(b"not the end", whole.index(b"Episode 1"))
    out = feed_prefix.cut_to_last_item(whole[:trap])
    parsed = _parse(out)
    assert not parsed.bozo, parsed.get("bozo_exception")
    assert [e.id for e in parsed.entries] == ["ep-0"]
    assert "not the end" in parsed.entries[0].description


def test_a_comment_holding_a_closing_tag_is_not_a_cut_point() -> None:
    doc = (b"<rss><channel><title>T</title><item><guid>a</guid></item>"
           b"<item><guid>b</guid><!-- </item> -->")
    out = feed_prefix.cut_to_last_item(doc)
    assert [e.id for e in _parse(out).entries] == ["a"]


def test_a_prefix_with_no_whole_item_is_refused_not_guessed() -> None:
    whole = _rss(2, pad=500)
    with pytest.raises(feed_prefix.NoCompleteItem):
        feed_prefix.cut_to_last_item(whole[: whole.index(b"</item>")])
    with pytest.raises(feed_prefix.NoCompleteItem):
        feed_prefix.cut_to_last_item(whole[: whole.index(b"<item>")])


def test_rss_1_items_list_is_not_an_item() -> None:
    doc = (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
           b'xmlns="http://purl.org/rss/1.0/"><channel><title>R</title><items>'
           b'<rdf:Seq><rdf:li resource="a"/></rdf:Seq></items></channel>'
           b'<item rdf:about="a"><title>A</title><link>https://x.example/a</link></item>'
           b'<item rdf:about="b"><title>B</title>')
    out = feed_prefix.cut_to_last_item(doc)
    assert out.rstrip().endswith(b"</item>\n</rdf:RDF>")
    assert [e.title for e in _parse(out).entries] == ["A"]


def test_max_items_stops_the_cut_early() -> None:
    out = feed_prefix.cut_to_last_item(_rss(10), max_items=3)
    assert [e.id for e in _parse(out).entries] == ["ep-0", "ep-1", "ep-2"]


# ─── net_safety: truncate instead of raise ────────────────────────────────

def test_the_blocking_fetcher_can_keep_a_prefix(dns) -> None:
    body = b"z" * 5000
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(net_safety.ResponseTooLarge):
            net_safety.fetch_bytes_sync(FEED_URL, max_bytes=1000, client=client)
        got = net_safety.fetch_bytes_sync(FEED_URL, max_bytes=1000, client=client, truncate=True)
        assert got.truncated and got.content == b"z" * 1000
        small = net_safety.fetch_bytes_sync(FEED_URL, max_bytes=9000, client=client, truncate=True)
        assert not small.truncated and small.content == body
        # The URL check is the same with truncate on.
        with pytest.raises(net_safety.UnsafeOutboundURL):
            net_safety.fetch_bytes_sync(
                "http://127.0.0.1:6370/v1/admin/snapshot", max_bytes=10, client=client, truncate=True
            )


@pytest.mark.asyncio
async def test_the_async_fetcher_can_keep_a_prefix(dns) -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"q" * 3000, headers={"ETag": '"v1"'})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        got = await net_safety.fetch_bytes(FEED_URL, max_bytes=100, client=client, truncate=True)
    assert got.truncated and len(got.content) == 100 and got.etag == '"v1"'


# ─── The poller's fetch ───────────────────────────────────────────────────

def test_a_feed_bigger_than_the_cap_is_read_newest_first(monkeypatch, dns, caplog) -> None:
    """The real cap: a generated feed of ~9.6 MB (1,600 episodes of ~6 KB)."""
    big = _rss(1600, pad=6000)
    assert len(big) > poller.MAX_FEED_BYTES
    _serve(monkeypatch, lambda r: httpx.Response(200, content=big))
    caplog.set_level(logging.INFO, logger=poller.__name__)
    parsed = poller._parse_feed_blocking(FEED_URL)
    assert parsed.feed.title == "Big Show"
    ids = [e.id for e in parsed.entries]
    assert ids == [f"ep-{i}" for i in range(poller.MAX_ITEMS_FROM_A_CUT_FEED)]
    assert poller._entry_enclosure(parsed.entries[0]) == "https://feeds.example.com/ep0.mp3"
    assert f"read its newest {poller.MAX_ITEMS_FROM_A_CUT_FEED} episodes" in caplog.text
    assert "oldest" not in caplog.text


def test_a_cut_atom_feed_is_read(monkeypatch, dns) -> None:
    monkeypatch.setattr(poller, "MAX_FEED_BYTES", 20_000)
    big = _atom(100, pad=500)
    _serve(monkeypatch, lambda r: httpx.Response(200, content=big))
    parsed = poller._parse_feed_blocking(FEED_URL)
    assert parsed.feed.title == "Atom Show"
    n = len(parsed.entries)
    assert 10 < n < 100
    assert [e.id for e in parsed.entries] == [f"urn:ep:{i}" for i in range(n)]


def test_an_oldest_first_cut_feed_is_still_recorded_and_logged(monkeypatch, dns, caplog) -> None:
    monkeypatch.setattr(poller, "MAX_FEED_BYTES", 20_000)
    big = _rss(200, pad=200, oldest_first=True)
    _serve(monkeypatch, lambda r: httpx.Response(200, content=big))
    caplog.set_level(logging.INFO, logger=poller.__name__)
    parsed = poller._parse_feed_blocking(FEED_URL)
    assert parsed.entries and parsed.entries[0].id == "ep-199"     # the oldest
    assert "lists its oldest episodes first" in caplog.text


def test_a_cut_with_no_whole_episode_says_too_large(monkeypatch, dns) -> None:
    monkeypatch.setattr(poller, "MAX_FEED_BYTES", 2_000)
    big = _rss(3, pad=5_000)
    _serve(monkeypatch, lambda r: httpx.Response(200, content=big))
    with pytest.raises(poller.FeedUnreadable) as err:
        poller._parse_feed_blocking(FEED_URL)
    assert err.value.reason == "too_large"
    assert poller.FAILURE_WORDS["too_large"] == "feed too large"


def test_a_feed_under_the_cap_is_unchanged(monkeypatch, dns) -> None:
    doc = _rss(5)
    _serve(monkeypatch, lambda r: httpx.Response(200, content=doc))
    assert poller.fetch_feed_bytes(FEED_URL) == doc


def test_an_unchanged_feed_is_not_read_again(monkeypatch, dns) -> None:
    doc = _rss(3)

    def handler(request):
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=doc, headers={
            "ETag": '"v1"', "Last-Modified": "Tue, 10 Nov 2026 12:00:00 GMT"})

    seen = _serve(monkeypatch, handler)
    first = poller._parse_feed_blocking(FEED_URL)
    assert len(first.entries) == 3
    # Not committed yet: the next fetch is still unconditional.
    poller._parse_feed_blocking(FEED_URL)
    assert "if-none-match" not in seen[1].headers
    poller.confirm_validators(FEED_URL)
    assert poller._parse_feed_blocking(FEED_URL) is poller.NOT_MODIFIED
    assert seen[2].headers["if-none-match"] == '"v1"'
    assert seen[2].headers["if-modified-since"] == "Tue, 10 Nov 2026 12:00:00 GMT"
    # An explicit whole-document read ignores the validators.
    assert poller.fetch_feed_bytes(FEED_URL) == doc
    assert "if-none-match" not in seen[3].headers


@pytest.mark.asyncio
async def test_a_not_modified_poll_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(poller, "_parse_feed_blocking", lambda url: poller.NOT_MODIFIED)
    calls = []

    class _Session:
        async def execute(self, statement, params=None):
            calls.append(str(statement))
            return SimpleNamespace(first=lambda: None, rowcount=0)

    assert await poller.poll_subscription(_Session(), {"id": 3, "feed_url": FEED_URL}) == 0
    assert len(calls) == 1 and "last_polled_at" in calls[0]
    assert "INSERT" not in calls[0]


def test_failure_reasons_are_plain_words() -> None:
    req = httpx.Request("GET", FEED_URL)
    cases = {
        poller.FeedUnreadable("not_found", "feed returned 404"): "feed not found",
        net_safety.ResponseTooLarge(FEED_URL, 10): "feed too large",
        httpx.ConnectError("boom", request=req): "couldn't reach the feed",
        net_safety.UnsafeOutboundURL("http://127.0.0.1/", "host resolves to a non-public address"):
            "the feed's address isn't allowed",
        ValueError("<raw parser text>"): "couldn't read the feed",
    }
    for exc, words in cases.items():
        assert poller.FAILURE_WORDS[poller.describe_poll_failure(exc)] == words


def test_an_http_error_and_a_non_feed_have_their_own_reasons(monkeypatch, dns) -> None:
    _serve(monkeypatch, lambda r: httpx.Response(404))
    with pytest.raises(poller.FeedUnreadable) as err:
        poller._parse_feed_blocking(FEED_URL)
    assert err.value.reason == "not_found"
    _serve(monkeypatch, lambda r: httpx.Response(500))
    with pytest.raises(poller.FeedUnreadable) as err:
        poller._parse_feed_blocking(FEED_URL)
    assert err.value.reason == "http_error"
    _serve(monkeypatch, lambda r: httpx.Response(200, content=b"<html><body><p>hi</body>"))
    with pytest.raises(poller.FeedUnreadable) as err:
        poller._parse_feed_blocking(FEED_URL)
    assert err.value.reason == "not_a_feed"


def test_a_show_never_read_is_named_by_its_host() -> None:
    assert poller._show_name({"title": "Big Show", "feed_url": FEED_URL}) == "Big Show"
    assert poller._show_name({"title": FEED_URL, "feed_url": FEED_URL}) == "the show at feeds.example.com"
    assert poller._show_name({"title": None, "feed_url": FEED_URL}) == "the show at feeds.example.com"


# ─── tick() reports the failures ──────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_tick_names_the_shows_it_could_not_read(db_session, monkeypatch) -> None:
    good = (await db_session.execute(text(
        "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) "
        "VALUES ('https://feeds.example.com/good.xml', 'Good Show', 2) RETURNING id"
    ))).scalar_one()
    bad = (await db_session.execute(text(
        "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) "
        "VALUES (:u, :u, 2) RETURNING id"), {"u": FEED_URL})).scalar_one()
    await db_session.commit()

    def parse(url):
        if url == FEED_URL:
            raise poller.FeedUnreadable("too_large", "feed is larger than 8388608 bytes and ...")
        poller._FETCHED[url] = ('"g1"', None)
        return SimpleNamespace(feed={"title": "Good Show"}, entries=[])

    async def no_downloads(session, pending):
        return 0

    monkeypatch.setattr(poller, "_parse_feed_blocking", parse)
    monkeypatch.setattr(poller, "download_pending", no_downloads)
    monkeypatch.setattr(poller.egress, "internet_turned_off", lambda: False)
    counts = await poller.PodcastFeedPoller().tick()
    assert counts["polled"] == 1
    assert counts["failed"] == 1
    assert counts["failures"] == [{
        "subscription_id": bad, "title": "the show at feeds.example.com",
        "reason": "too_large", "message": "feed too large",
    }]
    # The good feed's validators were confirmed after the commit.
    assert poller._conditional_headers("https://feeds.example.com/good.xml") == {"If-None-Match": '"g1"'}
    assert poller._conditional_headers(FEED_URL) == {}
    assert good


@pytest.mark.asyncio
async def test_tick_with_the_internet_off_reports_no_failures(monkeypatch) -> None:
    monkeypatch.setattr(poller.egress, "internet_turned_off", lambda: True)
    counts = await poller.PodcastFeedPoller().tick()
    assert counts["failed"] == 0 and counts["failures"] == []


# ─── POST /api/podcasts/poll carries them ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_poll_answer_includes_the_failures(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    import web.backend.api.podcasts as podcasts_api
    from domovoi.tests.auth_testkit import HEADER, install_fake_db, web_app

    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="dev-tok")
    monkeypatch.setattr(podcasts_api.egress, "internet_turned_off", lambda: False)

    async def tick(self):
        return {"polled": 2, "new": 4, "downloaded": 1, "evicted": 0, "failed": 1,
                "failures": [{"subscription_id": 9, "title": "Big Show",
                              "reason": "too_large", "message": "feed too large"}]}

    monkeypatch.setattr(poller.PodcastFeedPoller, "tick", tick)
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                           headers={HEADER: "dev-tok", "X-Requested-With": "test"}) as c:
        r = await c.post("/api/podcasts/poll", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    # The four counts every client reads are unchanged.
    assert {k: body[k] for k in ("polled", "new", "downloaded", "evicted")} == {
        "polled": 2, "new": 4, "downloaded": 1, "evicted": 0}
    assert body["failed"] == 1
    assert body["failures"] == [{"subscription_id": 9, "title": "Big Show",
                                 "reason": "too_large", "message": "feed too large"}]


# ─── News feeds share the cut ─────────────────────────────────────────────

def test_a_big_news_feed_is_read_newest_first(monkeypatch, dns) -> None:
    from domovoi.clients import news_feeds

    monkeypatch.setattr(news_feeds, "MAX_FEED_BYTES", 20_000)
    big = _rss(200, pad=200)
    _serve(monkeypatch, lambda r: httpx.Response(200, content=big))
    title, articles = news_feeds._parse_feed_blocking(FEED_URL)
    assert title == "Big Show"
    assert 10 < len(articles) < 200
    assert [a.guid for a in articles[:2]] == ["ep-0", "ep-1"]


# ─── gzip: asked for, and capped on the DECODED stream ────────────────────

def _gzip(data: bytes) -> bytes:
    import gzip

    return gzip.compress(data, compresslevel=6)


def _wire(data: bytes, encoding: str) -> httpx.Response:
    """A response streamed off the wire in 64 KB pieces (a body given as
    bytes is read whole by httpx up front, which no network does)."""
    def pieces():
        for i in range(0, len(data), 65536):
            yield data[i:i + 65536]

    return httpx.Response(200, content=pieces(), headers={"Content-Encoding": encoding})


def test_a_gzipped_big_feed_is_asked_for_and_cut_after_inflating(monkeypatch, dns) -> None:
    big = _rss(1600, pad=6000)
    seen = _serve(monkeypatch, lambda r: _wire(_gzip(big), "gzip"))
    parsed = poller._parse_feed_blocking(FEED_URL)
    assert "gzip" in seen[0].headers["accept-encoding"]
    assert [e.id for e in parsed.entries][:3] == ["ep-0", "ep-1", "ep-2"]
    assert len(parsed.entries) == poller.MAX_ITEMS_FROM_A_CUT_FEED


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw-deflate"])
def test_a_compression_bomb_stops_at_the_cap_in_bounded_memory(dns, encoding) -> None:
    """256 MB of zeros, ~250 KB on the wire. httpx's own decoder would
    inflate each 64 KB network chunk whole (~64 MB); the fetch inflates a
    bounded step at a time and never holds much more than the cap."""
    import tracemalloc
    import zlib

    if encoding == "gzip":
        z = zlib.compressobj(9, zlib.DEFLATED, zlib.MAX_WBITS | 16)
    elif encoding == "deflate":
        z = zlib.compressobj(9)
    else:
        z = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    block = b"\0" * (1 << 20)
    bomb = b"".join(z.compress(block) for _ in range(256)) + z.flush()
    assert len(bomb) < 1 << 20
    header = "gzip" if encoding == "gzip" else "deflate"
    transport = httpx.MockTransport(lambda r: _wire(bomb, header))
    cap = 1 << 20
    with httpx.Client(transport=transport) as client:
        with pytest.raises(net_safety.ResponseTooLarge):
            net_safety.fetch_bytes_sync(FEED_URL, max_bytes=cap, client=client)
        tracemalloc.start()
        try:
            got = net_safety.fetch_bytes_sync(FEED_URL, max_bytes=cap, client=client, truncate=True)
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert got.truncated and len(got.content) == cap and got.content == b"\0" * cap
    assert peak < 8 * cap, peak


def test_a_small_gzipped_body_comes_back_whole(dns) -> None:
    doc = _rss(4)
    transport = httpx.MockTransport(lambda r: _wire(_gzip(doc), "gzip"))
    with httpx.Client(transport=transport) as client:
        got = net_safety.fetch_bytes_sync(FEED_URL, max_bytes=1 << 20, client=client)
    assert got.content == doc and not got.truncated


# ─── RFC 5005 paging links don't break the cut ────────────────────────────

def test_paging_links_in_the_head_are_kept_and_not_followed(monkeypatch, dns) -> None:
    monkeypatch.setattr(poller, "MAX_FEED_BYTES", 20_000)
    links = (
        b'<atom:link rel="first" href="https://feeds.example.com/big.xml?offset=0"/>'
        b'<atom:link rel="last" href="https://feeds.example.com/big.xml?offset=0"/>'
        b'<atom:link rel="previous" href="https://feeds.example.com/big.xml?offset=0"></atom:link>'
        b'<atom:link rel="next-archive" href="https://feeds.example.com/archive?p=2"/>'
    )
    big = _rss(200, pad=200).replace(
        b'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        b'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        b'xmlns:atom="http://www.w3.org/2005/Atom">', 1,
    ).replace(b"<channel><title>Big Show</title>", b"<channel><title>Big Show</title>" + links, 1)
    seen = _serve(monkeypatch, lambda r: httpx.Response(200, content=big))
    parsed = poller._parse_feed_blocking(FEED_URL)
    assert not parsed.bozo, parsed.get("bozo_exception")
    assert parsed.feed.title == "Big Show"
    assert 10 < len(parsed.entries) < 200
    assert len(seen) == 1                       # one page, no archive walk
