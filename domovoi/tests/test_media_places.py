"""Media understanding, slice 7: places (domovoi/media_understanding/geocode.py,
the photo date analyser, search.py, web/backend/api/media_search.py and
media_understanding.py).

Offline reverse lookup against a tiny fixture town list (never the real
GeoNames files), the pinned opt-in fetch (size and SHA-256 checked; no fetch
while locations are off or with internet access "No"), naming a photo and
searching by its place, "at home" from the Home location without the
house's coordinates in any answer, turning locations off deleting every
position and name (with a confirmation), and who sees what.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import ai_features, egress
from domovoi.db.session import session_scope
from domovoi.media_understanding import geocode as G
from domovoi.media_understanding import photos, switches
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_media_photos import NIGHT, FakeOcr, FakeVision, _doc_matches, _drain, _jpeg, _put
from domovoi.tests.test_media_understanding import _items, _runner, _sync, house  # noqa: F401

REPO = Path(__file__).resolve().parents[2]

# ─── a tiny town list in GeoNames' own formats ──────────────────────────


def _row(gid: int, name: str, lat: float, lon: float, cc: str, admin1: str, code: str = "PPL") -> str:
    f = [str(gid), name, name, "", f"{lat}", f"{lon}", "P", code, cc, "", admin1, "", "", "", "5000",
         "", "10", "Etc/UTC", "2026-01-01"]
    return "\t".join(f)


TOWNS = [
    _row(1, "Portland", 45.52345, -122.67621, "US", "OR"),
    _row(2, "Beaverton", 45.48706, -122.80371, "US", "OR"),
    _row(3, "Pearl District", 45.5300, -122.6800, "US", "OR", code="PPLX"),   # a section: skipped
    _row(4, "Zürich", 47.36667, 8.55, "CH", "ZH"),
    _row(5, "Paris", 48.85341, 2.3488, "FR", "11"),
    _row(6, "Suva", -18.14161, 178.44149, "FJ", "01"),
    _row(7, "Denver", 39.73915, -104.9847, "US", "CO"),
]
REGIONS = "US.OR\tOregon\tOregon\t5744337\nCH.ZH\tZurich\tZurich\t2657895\nFR.11\tÎle-de-France\tIle-de-France\t3012874\n" \
          "FJ.01\tCentral\tCentral\t2205218\nUS.CO\tColorado\tColorado\t5417618\n"
COUNTRIES = "#ISO\tISO3\tISO-Numeric\tfips\tCountry\n" + "\n".join(
    f"{cc}\tXXX\t000\t{cc}\t{name}\tcap" for cc, name in
    (("US", "United States"), ("CH", "Switzerland"), ("FR", "France"), ("FJ", "Fiji"))) + "\n"


def _zip(rows: list[str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("cities1000.txt", "\n".join(rows) + "\n")
    return out.getvalue()


def _blobs() -> dict[str, bytes]:
    return {"cities1000.zip": _zip(TOWNS), "admin1CodesASCII.txt": REGIONS.encode(),
            "countryInfo.txt": COUNTRIES.encode()}


def _tiny_pins(monkeypatch) -> dict[str, bytes]:
    blobs = _blobs()
    pins = tuple(G.Pin(role, name, f"https://example.org/{name}", hashlib.sha256(data).hexdigest(), len(data))
                 for (name, data), role in zip(blobs.items(), ("towns", "regions", "countries")))
    monkeypatch.setattr(G, "PINS", pins)
    monkeypatch.setattr(G, "TOWNS", pins[0])
    monkeypatch.setattr(G, "REGIONS", pins[1])
    monkeypatch.setattr(G, "COUNTRIES", pins[2])
    G._reset_for_tests()
    return blobs


def _gaz() -> G.Gazetteer:
    b = _blobs()
    return G.Gazetteer.parse(b["cities1000.zip"], b["admin1CodesASCII.txt"].decode(), b["countryInfo.txt"].decode())


# ─── the lookup (pure, offline) ──────────────────────────────────────────


def test_the_nearest_town_is_found_offline_and_named_in_full() -> None:
    g = _gaz()
    assert len(g) == 6                                      # the PPLX section is skipped
    assert g.nearest(45.5, -122.675).label == "Portland, Oregon, United States"
    assert g.nearest(45.49, -122.80).label == "Beaverton, Oregon, United States"
    assert g.nearest(47.37, 8.54).label == "Zürich, Zurich, Switzerland"
    assert g.nearest(48.86, 2.35).label == "Paris, Île-de-France, France"
    # Across the date line: a point at -179.9 finds nothing within 50 km of
    # Suva (178.4), but the grid wraps rather than failing.
    assert g.nearest(-18.14, 178.5).label == "Suva, Central, Fiji"
    assert g.nearest(-18.14, -179.99) is None
    # The open sea, and nonsense.
    assert g.nearest(0.0, -140.0) is None
    assert g.nearest(float("nan"), 0.0) is None


def test_a_big_city_wins_inside_its_reach_and_a_town_inside_its_own() -> None:
    g = G.Gazetteer([
        G.Town("New York City", "New York", "United States", 40.71427, -74.00597, 8_800_000),
        G.Town("Weehawken", "New Jersey", "United States", 40.76955, -74.02042, 17_000),
        G.Town("Hoboken", "New Jersey", "United States", 40.74399, -74.03236, 50_000),
    ])
    # Times Square: Weehawken's centre is nearer, but it's well inside New York.
    assert g.nearest(40.758, -73.9855).name == "New York City"
    # Hoboken's own centre: deeper inside Hoboken than inside New York.
    assert g.nearest(40.7441, -74.0322).name == "Hoboken"
    # Outside every reach: the nearest.
    assert g.nearest(40.95, -74.10).name == "Weehawken"


def test_distances_and_home_point() -> None:
    assert abs(G.km_between(45.52345, -122.67621, 39.73915, -104.9847) - 1585) < 10
    assert G.home_point(None) is None
    assert G.home_point({"name": "Springfield", "lat": None, "lon": None}) is None
    assert G.home_point({"name": "Denver", "lat": 39.7, "lon": -105.0}) == (39.7, -105.0)


def test_the_real_pins_are_archived_geonames_snapshots() -> None:
    for pin in G.PINS:
        assert pin.url.startswith("https://web.archive.org/web/") and "id_/download.geonames.org/export/dump/" in pin.url
        assert pin.url.endswith(pin.file) and len(pin.sha256) == 64 and pin.size > 0
    assert G.REGIONS.sha256 == "2e4867f5397acb056ea26377601b57af37cb4c81399770225018e9e439e91ad3"   # build_places.py's pin
    from domovoi.config import settings

    assert Path(settings.place_names_dir).name == "place_names"


# ─── the opt-in fetch ────────────────────────────────────────────────────


def test_the_fetch_checks_size_and_sha256(tmp_path, monkeypatch) -> None:
    blobs = _tiny_pins(monkeypatch)
    calls = []

    def fetch(url, dest, *, sha256, size):
        calls.append(url)
        dest.write_bytes(blobs[dest.name])

    with egress.override_policy("always"):
        G.download(tmp_path, fetch=fetch)
        assert G.ready(tmp_path) and len(calls) == 3
        G.download(tmp_path, fetch=fetch)                  # nothing missing: no second fetch
    assert len(calls) == 3
    assert G.Gazetteer.from_files(tmp_path).nearest(45.5, -122.675).name == "Portland"

    bad = tmp_path / "bad"

    def tampered(url, dest, *, sha256, size):
        dest.write_bytes(b"x" * size)

    with egress.override_policy("always"), pytest.raises(G.TableUnavailable):
        G.download(bad, fetch=tampered)
    assert not G.ready(bad) and not any(bad.iterdir())
    assert "checksum" in (G.last_error() or "")


def test_no_fetch_when_internet_is_off(tmp_path, monkeypatch) -> None:
    _tiny_pins(monkeypatch)
    calls = []
    with egress.override_policy("never"), pytest.raises(egress.InternetTurnedOff):
        G.download(tmp_path, fetch=lambda *a, **k: calls.append(a))
    assert calls == [] and not any(tmp_path.iterdir())
    with egress.override_policy("never"):
        monkeypatch.setattr(G, "data_dir", lambda: tmp_path)
        assert "internet access is turned off" in G.problem()


class _HKRunner:
    def __init__(self, places: bool) -> None:
        self.sw = {**switches.DEFAULTS, "enabled": True, "places": places}


async def test_the_table_is_fetched_only_when_locations_are_on(monkeypatch) -> None:
    fetched = []
    G._reset_for_tests()

    async def fake_fetch() -> None:
        fetched.append(1)

    monkeypatch.setattr(G, "ready", lambda base=None: False)
    monkeypatch.setattr(G, "_fetch_table", fake_fetch)
    monkeypatch.setattr(G, "_fetch_task", None)
    await G.housekeeping(_HKRunner(places=False))
    assert fetched == [] and G._fetch_task is None
    await G.housekeeping(_HKRunner(places=True))
    await G._fetch_task
    assert fetched == [1]


def test_nothing_sends_a_coordinate_anywhere() -> None:
    """The only network code in geocode.py is the pinned fetch; the web
    never fetches; and no route module names a coordinate field."""
    src = (REPO / "domovoi" / "media_understanding" / "geocode.py").read_text(encoding="utf-8")
    assert "httpx" not in src and "requests" not in src
    for mod in ("media_search.py", "media_understanding.py", "faces.py"):
        web = (REPO / "web" / "backend" / "api" / mod).read_text(encoding="utf-8")
        assert "download(" not in web
        assert "gps" not in web and '"lat"' not in web and '"lon"' not in web


# ─── the database ────────────────────────────────────────────────────────


@pytest.fixture
async def ph(house, monkeypatch, tmp_path):  # noqa: F811
    """A house with locations on and the fixture town list in place."""
    monkeypatch.setattr(ai_features, "turned_off", lambda: False)
    blobs = _tiny_pins(monkeypatch)
    for name, data in blobs.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(G, "data_dir", lambda: tmp_path)
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "places": True})
        await s.execute(text("DELETE FROM household_settings WHERE key IN (:a, 'home_location')"),
                        {"a": G.STATE_KEY})
    photos.install(vision=FakeVision({"caption": "A bridge over a river.", "tags": ["bridge"], "has_text": False}),
                   ocr=FakeOcr())
    yield house
    async with session_scope() as s:
        await s.execute(text("DELETE FROM household_settings WHERE key IN (:a, 'home_location')"),
                        {"a": G.STATE_KEY})
        await s.execute(text("DELETE FROM devices WHERE device_id = 'placetest-kitchen'"))
    from domovoi import home_location

    home_location._remember(None)
    G._reset_for_tests()


async def _set_home(lat: float, lon: float, name: str) -> None:
    from domovoi import home_location

    async with session_scope() as s:
        await home_location.save(s, {"name": name, "lat": lat, "lon": lon, "country": "US"})


async def _search(q: str, home=None) -> list[dict[str, Any]]:
    from domovoi.media_understanding import search

    async with session_scope() as s:
        return (await search.search(s, search.parse_query(q), home=home))["items"]


@requires_db
async def test_a_photo_is_named_offline_and_found_by_its_place(ph) -> None:
    _put(ph, "trip/bridge.jpg", _jpeg(taken="2023:07:04 15:30:00", gps=True))     # 45.5, -122.675
    _put(ph, "nowhere.jpg", _jpeg(taken="2023:07:05 15:30:00", color="blue"))
    await _sync(ph)
    await _drain(_runner(now=lambda: NIGHT))
    items = await _items()
    it = items["trip/bridge.jpg"]
    assert it["gps"] == {"lat": 45.5, "lon": -122.675}
    assert it["place_name"] == "Portland, Oregon, United States"
    assert items["nowhere.jpg"]["place_name"] is None and items["nowhere.jpg"]["gps"] is None
    assert await _doc_matches(it["id"], "portland") and await _doc_matches(it["id"], "oregon")
    hits = await _search("photos from Portland")
    assert [h["path"] for h in hits] == ["trip/bridge.jpg"]
    assert hits[0]["place"] == "Portland, Oregon, United States" and hits[0]["near_home"] is None
    assert [h["path"] for h in await _search("pictures in Oregon 2023")] == ["trip/bridge.jpg"]
    assert await _search("photos from Paris") == []
    # No coordinate in a hit.
    assert "45.5" not in json.dumps(hits, default=str) and "122.6" not in json.dumps(hits, default=str)


@requires_db
async def test_an_accented_town_is_found_without_accents(ph, monkeypatch) -> None:
    from PIL import Image

    im = Image.new("RGB", (64, 64), "green")
    ex = Image.Exif()
    g = ex.get_ifd(photos.GPS_IFD)
    g[1], g[2], g[3], g[4] = "N", (47.0, 22.0, 0.0), "E", (8.0, 33.0, 0.0)
    out = io.BytesIO()
    im.save(out, "JPEG", exif=ex)
    _put(ph, "lake.jpg", out.getvalue())
    await _sync(ph)
    await _drain(_runner(now=lambda: NIGHT))
    assert (await _items())["lake.jpg"]["place_name"] == "Zürich, Zurich, Switzerland"
    assert [h["path"] for h in await _search("photos from zurich")] == ["lake.jpg"]
    assert [h["path"] for h in await _search("Zürich")] == ["lake.jpg"]


@requires_db
async def test_at_home_uses_the_home_location_without_storing_it(ph) -> None:
    _put(ph, "here.jpg", _jpeg(taken="2023:07:04 15:30:00", gps=True))           # Portland
    _put(ph, "away.jpg", _jpeg(taken="2023:07:04 15:30:00", color="blue"))
    await _sync(ph)
    await _drain(_runner(now=lambda: NIGHT))
    await _set_home(45.5234, -122.6762, "Portland, Oregon, US")
    home = (45.5234, -122.6762)
    hits = await _search("photos at home", home=home)
    assert [h["path"] for h in hits] == ["here.jpg"] and hits[0]["near_home"] is True
    # Somewhere else is home: nothing is near it.
    assert await _search("photos at home", home=(39.739, -104.985)) == []
    # Without a home point "home" is just a word (a folder called Home).
    _put(ph, "Home/sofa.jpg", _jpeg(color="white"))
    await _sync(ph)
    assert [h["path"] for h in await _search("at home")] == ["Home/sofa.jpg"]
    # The house's point is in no row.
    async with session_scope() as s:
        dump = json.dumps([dict(r) for r in (await s.execute(text(
            "SELECT gps, place_name, search_doc::text AS d FROM media_items"))).mappings().all()], default=str)
    assert "122.676" not in dump and "45.523" not in dump


@requires_db
async def test_turning_locations_on_reads_photos_dated_while_off(ph) -> None:
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "places": False})
    _put(ph, "p.jpg", _jpeg(taken="2023:07:04 15:30:00", gps=True))
    await _sync(ph)
    r = _runner(now=lambda: NIGHT)
    await _drain(r)
    assert (await _items())["p.jpg"]["gps"] is None
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "places": True})
    G.install()
    try:
        await _drain(r)
    finally:
        from domovoi.media_understanding import runner as R

        R.unregister_housekeeping("places")
    it = (await _items())["p.jpg"]
    assert it["place_name"] == "Portland, Oregon, United States" and it["state"] == "done"


@requires_db
async def test_positions_kept_before_the_table_came_are_named_later(ph, monkeypatch) -> None:
    real_ready = G.ready
    monkeypatch.setattr(G, "ready", lambda base=None: False)
    _put(ph, "p.jpg", _jpeg(taken="2023:07:04 15:30:00", gps=True))
    await _sync(ph)
    await _drain(_runner(now=lambda: NIGHT))
    it = (await _items())["p.jpg"]
    assert it["gps"] is not None and it["place_name"] is None
    monkeypatch.setattr(G, "ready", real_ready)
    async with session_scope() as s:
        assert await G.name_waiting(s) == 1
    assert (await _items())["p.jpg"]["place_name"] == "Portland, Oregon, United States"


@requires_db
async def test_turning_locations_off_deletes_every_position_and_name(ph, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client

    _put(ph, "p.jpg", _jpeg(taken="2023:07:04 15:30:00", gps=True))
    await _sync(ph)
    await _drain(_runner(now=lambda: NIGHT))
    item = (await _items())["p.jpg"]["id"]
    assert await _doc_matches(item, "portland")
    install_fake_db(monkeypatch, admin=True, sessions={"adm"})
    adm = bearer("adm")
    async with web_client() as c:
        st = (await c.get("/api/media-understanding", headers={**c.headers, **adm})).json()
        assert st["places"]["count"] == 1 and st["places"]["ready"] is True
        assert "lat" not in json.dumps(st)
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm}, json={"places": False})
        assert r.status_code == 409 and "1 photos" in r.json()["detail"]
        assert (await _items())["p.jpg"]["gps"] is not None
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm},
                        json={"places": False, "confirm_delete_places": True})
        assert r.status_code == 200 and r.json()["settings"]["places"] is False
    it = (await _items())["p.jpg"]
    assert it["gps"] is None and it["place_name"] is None
    assert not await _doc_matches(item, "portland") and await _doc_matches(item, "bridge")
    async with session_scope() as s:
        prog = (await s.execute(text(
            "SELECT progress FROM media_item_stages WHERE item_id = :i AND analyser = 'photo_date'"),
            {"i": item})).scalar()
        assert "places" not in prog
        assert (await G.read_state(s))["requeued"] is False
        # Off with nothing stored needs no confirmation.
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "places": True})
    async with web_client() as c:
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm}, json={"places": False})
        assert r.status_code == 200


@requires_db
async def test_who_sees_places(ph, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_client

    _put(ph, "p.jpg", _jpeg(taken="2023:07:04 15:30:00", gps=True))
    await _sync(ph)
    await _drain(_runner(now=lambda: NIGHT))
    await _set_home(45.5234, -122.6762, "Portland, Oregon, US")
    async with session_scope() as s:
        await s.execute(text("INSERT INTO devices (device_id, name, shared_screen) "
                             "VALUES ('placetest-kitchen', 'Kitchen', true)"))
    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    async with web_client() as c:
        # Guests: nothing.
        assert (await c.get("/api/media/search", params={"q": "portland"})).status_code == 401
        for h in ({HEADER: "household"}, bearer("adm")):
            r = await c.get("/api/media/search", params={"q": "photos at home"}, headers={**c.headers, **h})
            body = r.json()
            assert r.status_code == 200 and body["query"]["at_home"] is True, body
            assert [(x["place"], x["near_home"]) for x in body["items"]] == [("Portland, Oregon, United States", True)]
            assert "45.5" not in r.text and "122.6" not in r.text and '"gps"' not in r.text
            # A shared screen: no place, no near-home, no "at home" filter.
            r = await c.get("/api/media/search", params={"q": "bridge"},
                            headers={**c.headers, **h, "X-Domovoi-Device-Id": "placetest-kitchen"})
            body = r.json()
            assert [(x["place"], x["near_home"]) for x in body["items"]] == [(None, None)]
            r = await c.get("/api/media/search", params={"q": "photos at home"},
                            headers={**c.headers, **h, "X-Domovoi-Device-Id": "placetest-kitchen"})
            assert r.json()["query"]["at_home"] is False
        # The switch is admin only.
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, HEADER: "household"},
                        json={"places": False, "confirm_delete_places": True})
        assert r.status_code in (401, 403)
    # Locations off: no place and no near-home for anyone.
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "places": False})
        await G.delete_all(s)
    async with web_client() as c:
        r = await c.get("/api/media/search", params={"q": "bridge"}, headers={**c.headers, HEADER: "household"})
        assert [(x["place"], x["near_home"]) for x in r.json()["items"]] == [(None, None)]
