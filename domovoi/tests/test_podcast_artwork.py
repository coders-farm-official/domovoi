"""B11: the server fetches podcast artwork and serves it (domovoi.podcast_artwork).

DB-free. Every fetch goes through the real ``net_safety.fetch_bytes`` on an
``egress`` client, with a fake resolver (only ``art.example.com`` resolves,
to a public address) and an ``httpx.MockTransport`` that counts requests —
so "fetched once", "never fetched" and "refused" are assertions on what
would have gone out.
"""

from __future__ import annotations

import ipaddress
import json

import httpx
import pytest

from domovoi import egress, net_safety, podcast_artwork as pa
from domovoi.config import settings

PUBLIC_V4 = "93.184.216.34"
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
GIF = b"GIF89a" + b"\0" * 32
WEBP = b"RIFF\x24\0\0\0WEBPVP8 " + b"\0" * 32
HTML = b"<!doctype html><title>Not Found</title>"


@pytest.fixture(autouse=True)
def artwork_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "podcast_artwork_dir", str(tmp_path / "art"))
    monkeypatch.setattr(settings, "outbound_allow_hosts", "", raising=False)
    monkeypatch.setattr(pa, "_fill_tried", set())
    monkeypatch.setattr(pa, "_discover_urls", type(pa._discover_urls)())
    monkeypatch.setattr(pa, "_discovered_feeds", type(pa._discovered_feeds)())
    return tmp_path / "art"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    def fake_resolve(host: str):
        if host.lower() == "art.example.com":
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", fake_resolve)


@pytest.fixture
def server(monkeypatch):
    """Route every httpx client through a mock 'internet'. ``server.files``
    maps a path to (status, body); ``server.hits`` counts requests."""

    class _Server:
        def __init__(self) -> None:
            self.files: dict[str, tuple[int, bytes]] = {}
            self.hits: list[str] = []

        def handler(self, request: httpx.Request) -> httpx.Response:
            self.hits.append(str(request.url))
            status, body = self.files.get(request.url.path, (404, b"nope"))
            return httpx.Response(status, content=body)

    srv = _Server()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(srv.handler), **kw),
    )
    return srv


@pytest.mark.parametrize(
    ("data", "kind"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (GIF, "image/gif"), (WEBP, "image/webp"),
     (HTML, None), (b"", None), (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None)],
)
def test_only_jpeg_png_webp_gif_are_images(data, kind) -> None:
    assert pa.sniff_image(data) == kind


@pytest.mark.asyncio
async def test_artwork_is_stored_once_and_served_by_path(server, artwork_dir) -> None:
    server.files["/show.jpg"] = (200, JPEG)
    url = "https://art.example.com/show.jpg"

    first = await pa.ensure_artwork(7, url)
    again = await pa.ensure_artwork(7, url)

    assert first == again == artwork_dir / "7.img"
    assert first.read_bytes() == JPEG
    assert len(server.hits) == 1                       # the second call fetched nothing
    meta = json.loads((artwork_dir / "7.json").read_text(encoding="utf-8"))
    assert meta["content_type"] == "image/jpeg"
    assert meta["source_sha256"] == pa.source_sha(url)
    assert pa.cached_content_type(7) == "image/jpeg"
    path = pa.api_path(7, url)
    assert path == f"/api/podcasts/subscriptions/7/artwork?v={pa.source_sha(url)[:8]}"
    assert pa.api_path(7) == path


@pytest.mark.asyncio
async def test_a_new_source_url_is_fetched_again(server) -> None:
    server.files["/a.jpg"] = (200, JPEG)
    server.files["/b.png"] = (200, PNG)
    await pa.ensure_artwork(3, "https://art.example.com/a.jpg")
    old_path = pa.api_path(3)
    stored = await pa.ensure_artwork(3, "https://art.example.com/b.png")
    assert stored.read_bytes() == PNG
    assert pa.cached_content_type(3) == "image/png"
    assert len(server.hits) == 2
    assert pa.api_path(3) != old_path                  # ?v= moves, so clients reload
    # The list only shows stored artwork that came from the row's URL.
    assert pa.api_path(3, "https://art.example.com/a.jpg") is None


@pytest.mark.asyncio
async def test_a_non_image_is_refused_and_nothing_changes(server, artwork_dir) -> None:
    server.files["/ok.jpg"] = (200, JPEG)
    server.files["/page.html"] = (200, HTML)
    await pa.ensure_artwork(4, "https://art.example.com/ok.jpg")
    assert await pa.ensure_artwork(4, "https://art.example.com/page.html") is None
    assert (artwork_dir / "4.img").read_bytes() == JPEG   # the old image kept
    assert pa.api_path(4, "https://art.example.com/ok.jpg") is not None


@pytest.mark.asyncio
async def test_an_http_error_or_an_oversized_body_stores_nothing(server, monkeypatch) -> None:
    assert await pa.ensure_artwork(5, "https://art.example.com/missing.jpg") is None
    monkeypatch.setattr(pa, "ARTWORK_MAX_BYTES", 32)
    server.files["/huge.jpg"] = (200, JPEG + b"\0" * 1024)
    assert await pa.ensure_artwork(5, "https://art.example.com/huge.jpg") is None
    assert pa.cached_file(5) is None


@pytest.mark.asyncio
async def test_a_house_local_artwork_url_is_never_fetched(server) -> None:
    for url in ("http://127.0.0.1:6370/v1/admin/snapshot", "http://192.168.0.1/admin.png",
                "file:///etc/passwd", "https://does-not-resolve.example/x.jpg"):
        assert await pa.ensure_artwork(6, url) is None
    assert server.hits == []


@pytest.mark.asyncio
async def test_nothing_is_fetched_with_internet_access_turned_off(server, artwork_dir) -> None:
    server.files["/a.jpg"] = (200, JPEG)
    server.files["/b.jpg"] = (200, JPEG)
    await pa.ensure_artwork(8, "https://art.example.com/a.jpg")
    with egress.override_policy("never"):
        assert await pa.ensure_artwork(8, "https://art.example.com/b.jpg") is None
        assert await pa.fetch_image("https://art.example.com/b.jpg") is None
        assert pa.schedule_ensure(8, "https://art.example.com/b.jpg") is False
        # What is already stored stays, and is still served.
        assert await pa.ensure_artwork(8, "https://art.example.com/a.jpg") == artwork_dir / "8.img"
    assert len(server.hits) == 1


@pytest.mark.asyncio
async def test_forget_deletes_the_stored_artwork(server, artwork_dir) -> None:
    server.files["/a.jpg"] = (200, JPEG)
    await pa.ensure_artwork(9, "https://art.example.com/a.jpg")
    pa.forget(9)
    pa.forget(9)  # twice: no error
    assert pa.cached_file(9) is None and not (artwork_dir / "9.json").exists()


@pytest.mark.asyncio
async def test_the_list_fill_runs_once_per_subscription_and_url(server) -> None:
    server.files["/a.jpg"] = (200, JPEG)
    url = "https://art.example.com/a.jpg"
    assert pa.schedule_fill_once(11, url) is True
    assert pa.schedule_fill_once(11, url) is False
    for task in list(pa._tasks):
        await task
    assert pa.cached_file(11) is not None
    assert pa.schedule_fill_once(11, None) is False
    assert len(server.hits) == 1


# ─── Directory-search thumbnails ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unknown_discover_key_is_never_fetched(server) -> None:
    assert await pa.discover_artwork("0" * 32) is None
    assert await pa.discover_artwork("../../etc/passwd") is None
    assert pa.discover_cached("not-a-key") is None
    assert server.hits == []


@pytest.mark.asyncio
async def test_a_minted_key_is_fetched_once_then_served_from_disk(server, artwork_dir) -> None:
    server.files["/thumb.jpg"] = (200, JPEG)
    path = pa.discover_api_path("https://art.example.com/thumb.jpg")
    key = path.rsplit("/", 1)[1]
    assert path == f"/api/podcasts/discover/artwork/{key}" and len(key) == 32
    assert await pa.discover_artwork(key) == (JPEG, "image/jpeg")
    assert await pa.discover_artwork(key) == (JPEG, "image/jpeg")
    assert len(server.hits) == 1
    assert (artwork_dir / "discover" / key).read_bytes() == JPEG
    with egress.override_policy("never"):
        assert pa.discover_cached(key) == (JPEG, "image/jpeg")


@pytest.mark.asyncio
async def test_discover_keys_and_files_are_bounded(server, artwork_dir, monkeypatch) -> None:
    monkeypatch.setattr(pa, "DISCOVER_MAX", 3)
    keys = []
    for i in range(5):
        server.files[f"/t{i}.jpg"] = (200, JPEG)
        keys.append(pa.discover_key(f"https://art.example.com/t{i}.jpg"))
    assert list(pa._discover_urls) == keys[2:]         # the oldest two forgotten
    assert await pa.discover_artwork(keys[0]) is None
    for k in keys[2:]:
        assert await pa.discover_artwork(k) is not None
    assert len(list((artwork_dir / "discover").iterdir())) <= 3


def test_a_feed_found_by_search_remembers_its_artwork() -> None:
    pa.remember_discovered("https://feeds.example.com/show.xml", "https://art.example.com/600.jpg")
    assert pa.discovered_artwork_for(" https://feeds.example.com/show.xml ") == (
        "https://art.example.com/600.jpg"
    )
    assert pa.discovered_artwork_for("https://feeds.example.com/other.xml") is None
    pa.remember_discovered("https://feeds.example.com/x.xml", None)
    assert pa.discovered_artwork_for("https://feeds.example.com/x.xml") is None
