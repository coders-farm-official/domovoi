"""The fetchers that take a caller-chosen URL: who may ask, and which
URLs the server will actually go and get.

Podcast subscribe/poll and the news feed attach / re-validate sit on the
device tier (``X-Device-Token`` or an admin Bearer); model pull, cancel
and delete sit on the admin tier. Whatever the tier, the URL itself has
to pass ``domovoi.net_safety`` before a row is written or a byte moves.

DB-free throughout: the auth primitives come from the B0 test kit's fake
DB, ``resolve_host`` is patched, and each route module's
``session_scope`` is replaced by one that records whether the handler
ever reached the database (it must not, when the URL is refused).

Every test that asserts a refusal installs the fake DB in the SET-UP
state, because both tiers keep the pre-setup grace on purpose: on a fresh
install, before anyone has claimed admin, these routes are open. "401
without a device token" is a statement about a configured household.
"""

from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager

import httpx
import pytest

from domovoi import net_safety
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    bearer,
    install_fake_db,
    web_client,
)

PUBLIC_V4 = "93.184.216.34"

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"

# URLs no fetcher may take, whoever asks.
REFUSED_URLS = [
    "file:///etc/passwd",
    "http://127.0.0.1:11434/api/tags",
    "http://127.1:6370/v1/admin/snapshot",
    "http://0x7f000001:6370/v1/admin/snapshot",
    "http://[::1]:6370/v1/admin/snapshot",
    "http://10.1.2.3/feed.rss",
    "http://172.16.0.9/feed.rss",
    "http://192.168.1.50/feed.rss",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/feed.rss",
    "http://[fc00::1]/feed.rss",
    "http://localhost:6369/api/config/editable",
]


@pytest.fixture
def dns(monkeypatch):
    """Only ``feeds.example.com`` resolves, and only to a public address."""

    def fake_resolve(host: str):
        if host.lower() in ("feeds.example.com", "registry.example.com"):
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", fake_resolve)


@pytest.fixture(autouse=True)
def allowlist(monkeypatch):
    """``OUTBOUND_ALLOW_HOSTS`` pinned empty for every test in this module
    (so a value in this box's .env can never soften a refusal assertion),
    and settable by the handful of tests that are about it:
    ``allowlist("127.0.0.1:6391")``."""
    from domovoi.config import settings

    def set_to(raw: str) -> None:
        monkeypatch.setattr(settings, "outbound_allow_hosts", raw, raising=False)

    set_to("")
    return set_to


class _DBTouched(Exception):
    """Raised by the recording session scope: the handler got to the DB."""


@pytest.fixture
def no_db(monkeypatch):
    """Replace each route module's ``session_scope`` with one that records
    the attempt and refuses — so "nothing was written" is an assertion,
    not an assumption."""

    state = {"entered": False}

    @asynccontextmanager
    async def recording_scope():
        state["entered"] = True
        raise _DBTouched("handler reached the database")
        yield  # pragma: no cover

    from web.backend.api import models as models_api
    from web.backend.api import news as news_api
    from web.backend.api import podcasts as podcasts_api

    for module in (podcasts_api, news_api, models_api):
        monkeypatch.setattr(module, "session_scope", recording_scope)
    return state


# ═══ Device tier: podcasts ════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_podcast_subscribe_and_poll_need_a_device_token(
    monkeypatch, dns, no_db
) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        for path, body in (
            ("/api/podcasts/subscriptions", {"feed_url": "http://feeds.example.com/s.rss"}),
            ("/api/podcasts/poll", None),
        ):
            r = await web.post(path, json=body)
            assert r.status_code == 401, (path, r.text)
            assert HEADER.lower() in r.json()["detail"].lower()
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_a_stale_device_token_does_not_subscribe(monkeypatch, dns, no_db) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/podcasts/subscriptions",
            json={"feed_url": "http://feeds.example.com/s.rss"},
            headers={HEADER: "yesterdays-token"},
        )
    assert r.status_code == 401
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_the_dashboard_cookie_alone_does_not_subscribe(monkeypatch, dns, no_db) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/podcasts/subscriptions",
            json={"feed_url": "http://feeds.example.com/s.rss"},
            headers={"Cookie": f"{COOKIE}={ADMIN_TOKEN}"},
        )
    assert r.status_code == 403
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_a_device_token_gets_past_the_podcast_gate(monkeypatch, dns, no_db) -> None:
    """The gate is the tier, not a blanket refusal: with the household
    token the request reaches the handler (which this test stops at the
    database rather than writing anything)."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        for headers in ({HEADER: DEVICE_TOKEN}, bearer(ADMIN_TOKEN)):
            no_db["entered"] = False
            with pytest.raises(_DBTouched):
                await web.post(
                    "/api/podcasts/subscriptions",
                    json={"feed_url": "http://feeds.example.com/s.rss"},
                    headers=headers,
                )
            assert no_db["entered"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("url", REFUSED_URLS)
async def test_subscribing_to_a_house_local_feed_is_refused(monkeypatch, dns, no_db, url) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/podcasts/subscriptions",
            json={"feed_url": url},
            headers={HEADER: DEVICE_TOKEN},
        )
    assert r.status_code == 400, r.text
    assert "refusing this feed URL" in r.json()["detail"]
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_a_subscribable_feed_url_survives_the_check(monkeypatch, dns, no_db) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        with pytest.raises(_DBTouched):
            await web.post(
                "/api/podcasts/subscriptions",
                json={"feed_url": "https://feeds.example.com/show.rss"},
                headers={HEADER: DEVICE_TOKEN},
            )
    assert no_db["entered"] is True


@pytest.mark.asyncio
async def test_an_allowlisted_fixture_feed_gets_past_the_subscribe_gate(
    monkeypatch, dns, no_db, allowlist
) -> None:
    """The escape hatch, end to end at the route: the operator named
    127.0.0.1:6391, so the URL passes the check and the handler goes on to
    write the row — which is exactly what a test harness serving its own
    fixtures needs."""
    allowlist("127.0.0.1:6391")
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        with pytest.raises(_DBTouched):
            await web.post(
                "/api/podcasts/subscriptions",
                json={"feed_url": "http://127.0.0.1:6391/podcast/feed.xml"},
                headers={HEADER: DEVICE_TOKEN},
            )
    assert no_db["entered"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:6370/v1/admin/snapshot",   # a different port
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.2:6391/podcast/feed.xml",    # a different address
        "http://192.168.1.50:6391/podcast/feed.xml",
        "http://169.254.169.254:6391/latest/meta-data/",
    ],
)
async def test_the_allowlist_opens_only_the_endpoint_it_names(
    monkeypatch, dns, no_db, allowlist, url
) -> None:
    allowlist("127.0.0.1:6391")
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/podcasts/subscriptions",
            json={"feed_url": url},
            headers={HEADER: DEVICE_TOKEN},
        )
    assert r.status_code == 400, r.text
    assert "refusing this feed URL" in r.json()["detail"]
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_the_allowlist_is_server_config_not_a_request_parameter(
    monkeypatch, dns, no_db, allowlist
) -> None:
    """A caller cannot smuggle an entry in alongside the URL: the request
    body is parsed into the route's schema, and nothing in it reaches the
    setting."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    from domovoi.config import settings

    async with web_client() as web:
        for body in (
            {
                "feed_url": "http://127.0.0.1:6391/podcast/feed.xml",
                "outbound_allow_hosts": "127.0.0.1:6391",
            },
            {
                "feed_url": "http://127.0.0.1:6391/podcast/feed.xml",
                "allow_hosts": ["127.0.0.1:6391"],
            },
        ):
            r = await web.post(
                "/api/podcasts/subscriptions", json=body, headers={HEADER: DEVICE_TOKEN}
            )
            assert r.status_code == 400, r.text
            assert "refusing this feed URL" in r.json()["detail"]
    assert settings.outbound_allow_hosts == ""
    assert net_safety.allow_entries() == ()
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_discovery_hides_hits_the_server_would_refuse(monkeypatch, dns) -> None:
    """A directory can answer with anything; the page only gets the hits
    a subscribe would accept."""
    from web.backend.api import podcasts as podcasts_api

    payload = {
        "results": [
            {"collectionName": "Good", "feedUrl": "https://feeds.example.com/good.rss"},
            {"collectionName": "Local", "feedUrl": "http://127.0.0.1:8080/evil.rss"},
            {"collectionName": "File", "feedUrl": "file:///etc/passwd"},
        ]
    }

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *a, **kw):
            return _Resp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "AsyncClient", lambda **kw: _Client())
    out = await podcasts_api.discover(q="anything")
    assert [row["title"] for row in out] == ["Good"]


# ═══ Device tier: news ════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_attaching_a_news_feed_needs_a_device_token(
    monkeypatch, dns, no_db
) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/news/topics/1/feeds", json={"url": "https://feeds.example.com/x.rss"}
        )
        assert r.status_code == 401, r.text
        r = await web.post("/api/news/feeds/1/validate")
        assert r.status_code == 401, r.text
    assert no_db["entered"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("url", REFUSED_URLS)
async def test_attaching_a_house_local_news_feed_is_refused(monkeypatch, dns, no_db, url) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/news/topics/1/feeds",
            json={"url": url},
            headers={HEADER: DEVICE_TOKEN},
        )
    assert r.status_code == 400, r.text
    assert "refusing this feed URL" in r.json()["detail"]
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_an_allowlisted_fixture_feed_gets_past_the_news_attach_gate(
    monkeypatch, dns, no_db, allowlist
) -> None:
    allowlist("127.0.0.1:6391")
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    from web.backend.api import news as news_api

    async def valid(url: str) -> bool:
        return True

    # feed_is_valid runs before the DB; stub it so this asserts about the
    # URL gate, not about reaching a feed server that isn't running here.
    monkeypatch.setattr(news_api.news_feeds_client, "feed_is_valid", valid)
    async with web_client() as web:
        with pytest.raises(_DBTouched):
            await web.post(
                "/api/news/topics/1/feeds",
                json={"url": "http://127.0.0.1:6391/news/tech.xml"},
                headers={HEADER: DEVICE_TOKEN},
            )
    assert no_db["entered"] is True


@pytest.mark.asyncio
async def test_a_neighbouring_port_is_still_refused_by_the_news_attach_gate(
    monkeypatch, dns, no_db, allowlist
) -> None:
    allowlist("127.0.0.1:6391")
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        r = await web.post(
            "/api/news/topics/1/feeds",
            json={"url": "http://127.0.0.1:6369/api/config/editable"},
            headers={HEADER: DEVICE_TOKEN},
        )
    assert r.status_code == 400, r.text
    assert "refusing this feed URL" in r.json()["detail"]
    assert no_db["entered"] is False


# ═══ Admin tier: models ═══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pulling_cancelling_and_deleting_a_model_need_an_admin_session(
    monkeypatch, dns, no_db
) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        assert (await web.post("/api/models/pull", json={"model": "llama3.2:3b"})).status_code == 401
        assert (await web.post("/api/models/pull/1/cancel")).status_code == 401
        assert (await web.delete("/api/models/llama3.2:3b")).status_code == 401
        # The household device token is NOT enough: this tier is admin.
        r = await web.post(
            "/api/models/pull",
            json={"model": "llama3.2:3b"},
            headers={HEADER: DEVICE_TOKEN},
        )
        assert r.status_code == 401
        # …nor is the dashboard cookie on its own.
        r = await web.post(
            "/api/models/pull",
            json={"model": "llama3.2:3b"},
            headers={"Cookie": f"{COOKIE}={ADMIN_TOKEN}"},
        )
        assert r.status_code == 403
    assert no_db["entered"] is False


@pytest.mark.asyncio
async def test_an_admin_session_gets_past_the_model_gate(monkeypatch, dns, no_db) -> None:
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with web_client() as web:
        with pytest.raises(_DBTouched):
            await web.post(
                "/api/models/pull",
                json={"model": "llama3.2:3b"},
                headers=bearer(ADMIN_TOKEN),
            )
    assert no_db["entered"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "127.0.0.1:11434/library/llama3.2:3b",
        "localhost:5000/ns/model",
        "192.168.1.50:5000/ns/model",
        "169.254.169.254/ns/model",
        "[::1]:5000/ns/model",
    ],
)
async def test_a_pull_from_a_house_local_registry_is_refused(
    monkeypatch, dns, no_db, model
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with web_client() as web:
        r = await web.post(
            "/api/models/pull", json={"model": model}, headers=bearer(ADMIN_TOKEN)
        )
    assert r.status_code == 400, r.text
    assert "refusing to pull from this registry" in r.json()["detail"]
    assert no_db["entered"] is False


def test_only_a_reference_that_names_a_host_has_a_registry() -> None:
    from web.backend.api.models import registry_host

    assert registry_host("llama3.2:3b") is None
    assert registry_host("library/llama3.2:3b") is None
    assert registry_host("registry.ollama.ai/library/llama3.2") == "registry.ollama.ai"
    assert registry_host("127.0.0.1:11434/ns/m") == "127.0.0.1:11434"
    assert registry_host("localhost/ns/m") == "localhost"


# ═══ The podcast poller's own fetches ═════════════════════════════════════


def test_the_feed_fetch_refuses_a_house_local_feed(dns) -> None:
    from domovoi.workers import podcast_feed_poller as poller

    for url in ("file:///etc/passwd", "http://127.0.0.1:6370/v1/admin/snapshot"):
        with pytest.raises(net_safety.UnsafeOutboundURL):
            poller.fetch_feed_bytes(url)


def test_the_poller_reads_the_same_allowlist_as_the_web_routes(dns, allowlist) -> None:
    """The poller fetches in its own thread, in the CORE process — the
    reason the key is read inside net_safety and nowhere else. Subscribing
    and then never polling would be a harness that still can't be tested."""
    from domovoi.workers import podcast_feed_poller as poller

    allowlist("127.0.0.1:6391")
    assert net_safety.check_outbound_url("http://127.0.0.1:6391/podcast/feed.xml") is None
    # Still refused: the scheme rule, and every endpoint the entry did not name.
    for url in ("file:///etc/passwd", "http://127.0.0.1:6370/v1/admin/snapshot"):
        with pytest.raises(net_safety.UnsafeOutboundURL):
            poller.fetch_feed_bytes(url)


def test_the_news_feed_parse_refuses_a_house_local_feed(dns) -> None:
    """``parse_feed`` is best-effort by contract: a refused URL comes back
    as "no articles", never as a crash — and feedparser never sees it."""
    import asyncio

    from domovoi.clients import news_feeds

    title, articles = asyncio.run(news_feeds.parse_feed("file:///etc/passwd"))
    assert (title, articles) == (None, [])
    title, articles = asyncio.run(news_feeds.parse_feed("http://169.254.169.254/meta"))
    assert (title, articles) == (None, [])


@pytest.mark.asyncio
async def test_an_enclosure_download_stops_at_the_byte_cap(monkeypatch, dns, tmp_path) -> None:
    from domovoi.config import settings
    from domovoi.workers import podcast_feed_poller as poller

    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    monkeypatch.setattr(poller, "MAX_ENCLOSURE_BYTES", 4096)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"\0" * 20000)
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )

    session = _RecordingSession()
    ok = await poller.download_episode(
        session,
        {
            "id": 7,
            "subscription_id": 1,
            "guid": "ep-7",
            "title": "Episode 7",
            "sub_title": "Show",
            "enclosure_url": "https://feeds.example.com/ep7.mp3",
        },
    )
    assert ok is False
    assert any("failed" in sql for sql in session.statements)
    assert list(tmp_path.rglob("*.mp3")) == []


@pytest.mark.asyncio
async def test_an_enclosure_under_the_cap_lands_on_disk(monkeypatch, dns, tmp_path) -> None:
    from domovoi.config import settings
    from domovoi.workers import podcast_feed_poller as poller

    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    monkeypatch.setattr(poller, "MAX_ENCLOSURE_BYTES", 4096)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"\0" * 1000)
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )

    session = _RecordingSession()
    ok = await poller.download_episode(
        session,
        {
            "id": 8,
            "subscription_id": 1,
            "guid": "ep-8",
            "title": "Episode 8",
            "sub_title": "Show",
            "enclosure_url": "https://feeds.example.com/ep8.mp3",
        },
    )
    assert ok is True
    written = list(tmp_path.rglob("*.mp3"))
    assert len(written) == 1 and written[0].stat().st_size == 1000


@pytest.mark.asyncio
async def test_an_enclosure_pointing_into_the_house_is_never_fetched(
    monkeypatch, dns, tmp_path
) -> None:
    from domovoi.config import settings
    from domovoi.workers import podcast_feed_poller as poller

    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))

    def refuse(request):  # pragma: no cover — reaching this IS the failure
        raise AssertionError(f"fetched {request.url}")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw),
    )

    session = _RecordingSession()
    ok = await poller.download_episode(
        session,
        {
            "id": 9,
            "subscription_id": 1,
            "guid": "ep-9",
            "title": "Episode 9",
            "sub_title": "Show",
            "enclosure_url": "http://127.0.0.1:6370/v1/admin/snapshot",
        },
    )
    assert ok is False
    assert list(tmp_path.rglob("*")) != [] or True  # dest dir may exist, file must not
    assert list(tmp_path.rglob("*.mp3")) == []


class _RecordingSession:
    """Minimal async session double for the downloader: records the SQL it
    was handed and answers commit()."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return None

    async def commit(self) -> None:
        pass
