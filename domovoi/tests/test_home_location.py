"""Home location (1.4.1): Settings → Household → Home location.

* the bundled town list (domovoi/places, GeoNames cities15000) is whole
  and in order; suggestions are prefix, accent-blind, biggest first;
* the place round-trips through household_settings; only an admin sets it,
  any household device reads it;
* the one-time carry-over from the old ``news_location`` setting: a
  matched town, an unmatched name (kept, no coordinates, nudge), nothing;
* ``core_config.news_location`` is the Home location's name; news and
  weather read the Home location; SDK 1.13's household / places calls;
* the settings card and the settings search (JSX, driven in node).
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi import home_location, places
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]
DENVER = {"name": "Denver, Colorado, US", "lat": 39.7392, "lon": -104.9847, "country": "US"}


# ─── the town list ───────────────────────────────────────────────────────


def test_the_data_file_is_whole_and_biggest_first() -> None:
    with gzip.open(places.DATA_FILE, "rt", encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert tuple(lines[0].split("\t")) == places.HEADER
    rows = [line.split("\t") for line in lines[1:]]
    assert len(rows) > 30000
    assert all(len(r) == 7 for r in rows)
    pops = [int(r[6]) for r in rows]
    assert pops == sorted(pops, reverse=True)
    for name, _ascii, _a1, cc, lat, lon, _pop in rows:
        assert name and len(cc) == 2 and cc.isupper()
        assert -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180
    # Small enough to ship in the repo.
    assert places.DATA_FILE.stat().st_size < 1_500_000
    assert len(places.cities()) == len(rows)


def test_the_build_script_pins_its_sources() -> None:
    src = (REPO_ROOT / "scripts" / "build_places.py").read_text(encoding="utf-8")
    for name in ("cities15000.zip", "admin1CodesASCII.txt"):
        assert name in src
    assert src.count("web.archive.org/web/") == 2 and "sha256" in src


def test_suggest_is_prefix_accent_blind_and_biggest_first() -> None:
    assert places.suggest("denv", 1) == [DENVER]
    assert places.suggest("Denver, co")[0]["name"] == "Denver, Colorado, US"
    # Accents either way round.
    assert places.suggest("sao paulo", 1)[0]["name"] == places.suggest("São Paulo", 1)[0]["name"]
    assert places.suggest("zurich", 1)[0]["name"].startswith("Zürich")
    # Biggest first: the first "Spring…" towns are the biggest ones.
    hits = places.suggest("springf", 8)
    pops = {c.label: c.population for c in places.cities()}
    assert [pops[h["name"]] for h in hits] == sorted((pops[h["name"]] for h in hits), reverse=True)
    assert places.suggest("", 8) == [] and places.suggest("zzzzqq", 8) == []
    assert len(places.suggest("san", 50)) == places.MAX_LIMIT


def test_match_takes_only_a_clear_town() -> None:
    assert places.match("Denver") == DENVER
    assert places.match("denver, colorado") == DENVER
    assert places.match("Springfield") is None                     # several, none clearly biggest
    assert places.match("Springfield, Illinois")["country"] == "US"
    assert places.match("Nowhereville") is None
    assert places.match("") is None


# ─── stored, carried over, read ──────────────────────────────────────────


@requires_db
async def test_the_setting_round_trips(db_session) -> None:
    assert await home_location.load(db_session) is None
    saved = await home_location.save(db_session, DENVER)
    assert {k: saved[k] for k in DENVER} == DENVER
    assert {k: (await home_location.load(db_session))[k] for k in DENVER} == DENVER
    assert home_location.name() == "Denver, Colorado, US"
    # A typed name a town matches gets its coordinates.
    typed = await home_location.save(db_session, {"name": "Denver, CO"})
    assert typed["lat"] == DENVER["lat"]
    # One no town matches is kept, without coordinates: the card asks to pick.
    odd = await home_location.save(db_session, {"name": "Springfield"})
    assert odd["lat"] is None and home_location.needs_pick(odd)
    assert await home_location.save(db_session, {"name": ""}) is None
    assert await home_location.load(db_session) is None
    with pytest.raises(home_location.InvalidPlace):
        await home_location.save(db_session, {"name": "x", "lat": 91, "lon": 0})
    with pytest.raises(home_location.InvalidPlace):
        await home_location.save(db_session, {"name": "x", "lat": 1})
    with pytest.raises(home_location.InvalidPlace):
        await home_location.save(db_session, {"name": "x", "country": "USA"})


@requires_db
async def test_carry_over_a_matched_name(db_session) -> None:
    carried = await home_location.carry_over(db_session, "Denver")
    assert {k: carried[k] for k in DENVER} == DENVER and carried["carried_over"]
    # Once: the old text is never read again, even after a clear.
    await home_location.save(db_session, {"name": ""})
    assert await home_location.carry_over(db_session, "Denver") is None
    assert await home_location.load(db_session) is None


@requires_db
async def test_carry_over_an_unmatched_name_keeps_the_text(db_session) -> None:
    carried = await home_location.carry_over(db_session, "  Springfield ")
    assert carried["name"] == "Springfield" and carried["lat"] is None and carried["lon"] is None
    loaded = await home_location.load(db_session)
    assert loaded["carried_over"] and home_location.needs_pick(loaded)


@requires_db
async def test_carry_over_nothing(db_session) -> None:
    assert await home_location.carry_over(db_session, "") is None
    assert await home_location.load(db_session) is None
    # The empty row marks it done: a later boot doesn't carry anything.
    assert await home_location.carry_over(db_session, "Denver") is None


@requires_db
async def test_an_existing_home_location_is_never_overwritten(db_session) -> None:
    await home_location.save(db_session, {"name": "Springfield, Illinois"})
    assert await home_location.carry_over(db_session, "Denver") is None
    assert (await home_location.load(db_session))["name"] == "Springfield, Illinois, US"


@requires_db
async def test_news_location_is_an_alias_for_the_home_location_name(db_session) -> None:
    from domovoi.sdk.coreconfig import CoreConfigView

    view = CoreConfigView()
    assert view.news_location == ""
    await home_location.save(db_session, DENVER)
    assert view.news_location == "Denver, Colorado, US"


@requires_db
async def test_weather_asks_for_the_home_location(db_session) -> None:
    from domovoi import weather
    from domovoi.models import Context

    assert await weather.household_place() is None
    await home_location.save(db_session, DENVER)
    await db_session.commit()
    assert await weather.household_place() == {
        "name": "Denver, Colorado, US", "lat": 39.7392, "lon": -104.9847, "query": "Denver"}
    await home_location.save(db_session, {"name": "Springfield"})
    await db_session.commit()
    assert (await weather.household_place())["query"] == "Springfield"

    class _Searx:
        asked: list = []

        async def weather(self, place):
            self.asked.append(place)
            return f"sunny in {place}"

    from unittest.mock import patch

    await home_location.save(db_session, {"name": ""})
    await db_session.commit()
    with patch("domovoi.weather.get_searxng_client", lambda: _Searx()):
        said = await weather.answer_weather("what's the weather", Context(room_id="kitchen"))
    assert "Settings → Household → Home location" in said and _Searx.asked == []


@requires_db
async def test_the_sdk_reads_the_home_location_and_suggests_towns(db_session) -> None:
    from domovoi.sdk import API_VERSION, build_sdk

    assert tuple(int(x) for x in API_VERSION.split(".")) >= (1, 13, 0)
    sdk = build_sdk("wx-test")
    assert await sdk.household.home_location() is None
    await home_location.save(db_session, DENVER)
    await db_session.commit()
    assert await sdk.household.home_location() == DENVER
    assert sdk.places.suggest("denv", limit=1) == [DENVER]


@requires_db
async def test_the_web_routes_answer(db_session) -> None:
    from web.backend.api import home_location as api

    empty = await api.get_home_location()
    assert empty["place"] is None and not empty["needs_pick"] and "GeoNames" in empty["attribution"]
    put = await api.put_home_location(api.PlaceIn(**DENVER))
    assert put["place"] == DENVER and not put["needs_pick"]
    assert (await api.get_home_location())["place"] == DENVER
    assert (await api.suggest_places(q="denv", limit=1))["places"] == [DENVER]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await api.put_home_location(api.PlaceIn(name="x", lat=1.0))
    assert e.value.status_code == 422
    await api.put_home_location(api.PlaceIn(name=""))


@requires_db
async def test_local_news_follows_the_home_location(db_session) -> None:
    from domovoi import news_service

    await news_service.ensure_house_feeds(db_session)       # no place: no local discovery
    from sqlalchemy import text

    assert (await db_session.execute(text("SELECT count(*) FROM news_feeds WHERE scope = 'local'"))).scalar() == 0


# ─── who may ─────────────────────────────────────────────────────────────


def _deps(path: str, method: str) -> set:
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app

    def calls(dependant):
        for dep in getattr(dependant, "dependencies", ()):
            if dep.call is not None:
                yield dep.call
            yield from calls(dep)

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return set(calls(rc.dependant))
    raise AssertionError(f"{method} {path} is not a route")


def test_an_admin_changes_it_and_household_devices_read_it() -> None:
    from domovoi.admin_auth import require_admin_mutation, require_device_read

    assert require_device_read in _deps("/api/household/home-location", "GET")
    assert require_admin_mutation in _deps("/api/household/home-location", "PUT")
    assert require_device_read in _deps("/api/places/suggest", "GET")


def test_the_old_field_is_gone_from_advanced() -> None:
    from domovoi.config_schema import EDITABLE_FIELDS

    assert "news_location" not in {f.name for f in EDITABLE_FIELDS}


# ─── the card and the search (JSX in node) ───────────────────────────────

_SEARCH = r"""
  const w = h.global('window');
  const index = w.settingsSearchIndex(w.settingsLayout().groups, []);
  const find = (q) => w.settingsSearch(index, q).map((x) => x.route);
  const out = {};
  for (const q of ['news', 'location', 'town', 'city', 'weather', 'home']) out[q] = find(q);
  out.household = w.settingsLayout().groups[0].sections.map((s) => s.id);
  return out;
"""

_CARD = r"""
  h.render(); await h.settle(); h.rerender();
  const before = h.text().join(' ');
  h.change({ placeholder: 'start typing your town · Denver…' }, 'den'); await h.settle(); h.rerender();
  h.click((e) => e.type === 'a' && e.props && e.props.role === 'option'); await h.settle(); h.rerender();
  return { before, calls: h.calls };
"""

SUGGEST = {"places": [DENVER], "attribution": "x"}
SCENARIOS = {
    "search": {
        "files": ["web/static/components.jsx", "web/static/home_location.jsx", "web/static/settings.jsx"],
        "component": "SettingsPage", "script": _SEARCH,
    },
    "card": {
        "files": ["web/static/components.jsx", "web/static/home_location.jsx"],
        "component": "HomeLocationPanel", "script": _CARD,
        "api": {
            "GET /api/household/home-location": {
                "place": {"name": "Springfield", "lat": None, "lon": None, "country": None},
                "needs_pick": True, "carried_over": True, "attribution": "x"},
            "GET /api/places/suggest?q=den&limit=8": SUGGEST,
            "PUT /api/household/home-location": {"place": DENVER, "needs_pick": False},
        },
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    spec = tmp_path_factory.mktemp("home-location") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    harness = Path(__file__).with_name("jsx_interact_harness.js")
    proc = subprocess.run([node, str(harness), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_settings_search_finds_home_location_by_its_old_words(driven) -> None:
    r = driven["search"]
    for q in ("news", "location", "town", "city", "weather", "home"):
        assert "settings/home-location" in r[q], (q, r[q])
    assert r["household"][:4] == ["rooms", "people", "phones-and-browsers", "home-location"]


def test_the_card_nudges_a_carried_name_credits_geonames_and_saves_a_pick(driven) -> None:
    r = driven["card"]
    assert "Carried over from the old news location" in r["before"]
    assert "Pick your town from the list" in r["before"]
    assert "GeoNames" in r["before"] and "CC BY 4.0" in r["before"]
    assert {"method": "PUT", "path": "/api/household/home-location", "body": DENVER} in r["calls"]


def test_the_home_location_card_pads_its_field_like_other_settings_cards():
    """The town field, its "Now:" line and the attribution sit inside the
    card's standard inner padding (12px 16px), not flush against its edges."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web" / "static" / "home_location.jsx").read_text(encoding="utf-8")
    block = src[src.index('data-settings-mark="home-location"'):]
    assert block[:block.index(">")].count("padding: '12px 16px'") == 1
