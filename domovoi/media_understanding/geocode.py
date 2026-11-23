"""Places (media understanding slice 7, design MEDIA-UNDERSTANDING §2, §8
and §13 Q7; owner answers 2026-11-22: locations are OFF by default, an
admin turns them on).

**What it does.** While "Use photo locations" is on, the photo date
analyser (:mod:`.photos`) keeps a photo's EXIF GPS (``media_items.gps``)
and names the town it was taken in (``media_items.place_name``, e.g.
"Portland, Oregon, United States"), and the name goes into the photo's
``search_doc`` so "photos from Portland" finds it. A search for "at home"
keeps the photos taken near the household's Home location (Settings →
Household; :mod:`domovoi.home_location`), worked out inside the query:
the house's coordinates are never copied into a result or a row.

**Offline, always.** The town is found on this server, in GeoNames'
``cities1000`` table (every town over 1,000 people), with
``admin1CodesASCII`` (state and region names) and ``countryInfo``
(country names). GeoNames data is CC BY 4.0; docs/LICENSES.md and the
Settings card credit it. A photo's coordinates are never sent anywhere:
there is no online geocoder.

**The table arrives on opt-in.** GeoNames republishes its files daily, so
the three files are Internet Archive snapshots, each pinned by URL, size
and SHA-256 (as ``scripts/build_places.py`` pins the Home location list).
The core's runner fetches them (:func:`download`, through the egress gate)
only after an admin turns locations on, and only when they aren't already
here; under internet access "No" nothing is fetched and :func:`problem`
says why. A file that doesn't match its pin is never moved into place.

**Turning locations off** deletes every stored GPS position and place name
(:func:`delete_all`; the settings route asks for a confirmation first) and
rebuilds those photos' search text. The downloaded table is public data and
stays, so turning it on again doesn't fetch it again.

**Who sees what.** Coordinates never leave the database through any
route: the search returns the place's name and whether it was near home,
never a latitude or longitude, and a shared screen gets neither
(web/backend/api/media_search.py).

Core-internal (the web process reads :func:`ready`, :func:`problem` and
calls :func:`delete_all`; it never fetches).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

from domovoi.media_understanding import switches

log = logging.getLogger(__name__)

#: Internet Archive snapshots of GeoNames' daily files (raw bytes: ``id_``).
_ARCHIVE = "https://web.archive.org/web/"


@dataclass(frozen=True)
class Pin:
    role: str           # "towns" | "regions" | "countries"
    file: str
    url: str
    sha256: str
    size: int


TOWNS = Pin(
    role="towns",
    file="cities1000.zip",
    url=_ARCHIVE + "20261116171636id_/download.geonames.org/export/dump/cities1000.zip",
    sha256="46a35f52226f34dd56b64d57b4e9bb380a8ad06605354f8095ea9cdc561aba2c",
    size=11_096_946,
)
REGIONS = Pin(
    role="regions",
    file="admin1CodesASCII.txt",
    url=_ARCHIVE + "20261117201220id_/download.geonames.org/export/dump/admin1CodesASCII.txt",
    sha256="2e4867f5397acb056ea26377601b57af37cb4c81399770225018e9e439e91ad3",
    size=151_571,
)
COUNTRIES = Pin(
    role="countries",
    file="countryInfo.txt",
    url=_ARCHIVE + "20261107045531id_/download.geonames.org/export/dump/countryInfo.txt",
    sha256="79f8f59ea01cbb40438bf1856dd6c2ff201f03f8fc72d0530b2646aaf603ef04",
    size=32_112,
)
PINS: tuple[Pin, ...] = (TOWNS, REGIONS, COUNTRIES)

#: A photo further than this from every town gets no place name.
MAX_KM = 50.0
#: A town "reaches" this many km per square root of its population
#: (New York about 15 km, Paris 7, a town of 10,000 people 0.5): a photo
#: within a town's reach is named after the town it is deepest inside,
#: so Times Square is New York City, not the nearer centre of a small
#: town across the river. Outside every reach, the nearest town.
REACH_KM_PER_SQRT_POP = 0.005
#: "At home": within this distance of the Home location. The setting holds
#: a town (its centre, from the town list), not the house's address, so
#: this is "in or around the home town".
HOME_KM = 10.0
#: After a failed fetch, the next try waits this long.
RETRY_AFTER_SEC = 600.0
#: Where the runner keeps its bookkeeping (fetch error, requeued).
STATE_KEY = "media_places_state"
#: GeoNames feature codes that aren't a town of their own: a section of
#: a town, a historical, abandoned or destroyed place.
SKIP_CODES = frozenset({"PPLX", "PPLH", "PPLQ", "PPLW"})
EARTH_KM = 6371.0088


class TableUnavailable(RuntimeError):
    """A file of the town table isn't here and couldn't be fetched."""


def data_dir() -> Path:
    from domovoi.config import settings

    return Path(settings.place_names_dir)


def path_of(pin: Pin, base: Path | None = None) -> Path:
    return (base or data_dir()) / pin.file


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_verified: set[tuple[str, int, int]] = set()


def on_disk(pin: Pin, base: Path | None = None) -> bool:
    """Whether ``pin``'s file is here with its pinned size and SHA-256."""
    p = path_of(pin, base)
    try:
        st = p.stat()
    except OSError:
        return False
    if st.st_size != pin.size or p.is_symlink():
        return False
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _verified:
        return True
    if _sha256_file(p) != pin.sha256:
        return False
    _verified.add(key)
    return True


def ready(base: Path | None = None) -> bool:
    return all(on_disk(p, base) for p in PINS)


_lock = threading.Lock()
_last_failure: tuple[float, str] | None = None


def may_retry() -> bool:
    return _last_failure is None or time.monotonic() - _last_failure[0] >= RETRY_AFTER_SEC


def last_error() -> str | None:
    return _last_failure[1] if _last_failure else None


def _default_fetch(url: str, dest: Path, *, sha256: str, size: int) -> None:
    from domovoi.satellite_media.fetchers import _fetch_pinned

    _fetch_pinned(url, dest, sha256=sha256, size=size)


def download(base: Path | None = None, *, fetch: Callable[..., None] | None = None) -> Path:
    """Fetch every missing file of the town table into ``base``: through
    the egress gate, each moved into place only once its size and SHA-256
    match its pin, and checked again on disk afterwards. Raises
    ``egress.InternetTurnedOff`` under "No" (before any request),
    :class:`TableUnavailable` otherwise. Blocking: run it in a thread."""
    global _last_failure
    from domovoi import egress

    base = base or data_dir()
    fetch = fetch or _default_fetch
    with _lock:
        missing = [p for p in PINS if not on_disk(p, base)]
        if not missing:
            return base
        for pin in missing:
            egress.require_destination(pin.url)     # raises (and logs) under "No"
        base.mkdir(parents=True, exist_ok=True)
        for pin in missing:
            target = path_of(pin, base)
            log.info("places: downloading the %s list (%.1f MB, one-time)", pin.role, pin.size / 1e6)
            try:
                fetch(pin.url, target, sha256=pin.sha256, size=pin.size)
            except egress.InternetTurnedOff:
                raise
            except Exception as e:  # noqa: BLE001 — said in the status, retried later
                why = f"the {pin.role} list: {str(e) or type(e).__name__}"
                _last_failure = (time.monotonic(), why)
                log.warning("places: %s", why)
                raise TableUnavailable(why) from None
            if not on_disk(pin, base):
                target.unlink(missing_ok=True)
                why = f"the {pin.role} list did not match its pinned checksum"
                _last_failure = (time.monotonic(), why)
                raise TableUnavailable(why)
        _last_failure = None
        log.info("places: town list verified")
        return base


def problem() -> str | None:
    """Why places can't be named yet, in words for the household; None
    when the town list is here."""
    from domovoi import egress

    if ready():
        return None
    if egress.internet_turned_off():
        return ("Naming places needs a list of towns (about 11 MB) that isn't on this server yet, "
                "and internet access is turned off, so Domovoi can't download it.")
    if _last_failure is not None:
        return f"Domovoi couldn't download the list of towns ({_last_failure[1]}). It tries again later."
    return "Domovoi is downloading the list of towns (about 11 MB, one time)."


def _reset_for_tests() -> None:
    global _last_failure, _table
    _last_failure = None
    _verified.clear()
    _table = None


# ─── the table ───────────────────────────────────────────────────────────


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class Town:
    name: str
    region: str
    country: str
    lat: float
    lon: float
    population: int = 0

    @property
    def reach_km(self) -> float:
        return REACH_KM_PER_SQRT_POP * math.sqrt(max(self.population, 0))

    @property
    def label(self) -> str:
        parts = [self.name]
        for p in (self.region, self.country):
            if p and p.casefold() not in (x.casefold() for x in parts):
                parts.append(p)
        return ", ".join(parts)


class Gazetteer:
    """Towns on a one-degree grid, for the nearest town to a point."""

    def __init__(self, towns: list[Town]) -> None:
        self.towns = towns
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i, t in enumerate(towns):
            self.cells.setdefault((math.floor(t.lat), math.floor(t.lon)), []).append(i)

    def __len__(self) -> int:
        return len(self.towns)

    def nearest(self, lat: float, lon: float, max_km: float = MAX_KM) -> Town | None:
        """The town ``(lat, lon)`` is in: the one it is deepest inside
        (distance over reach, :data:`REACH_KM_PER_SQRT_POP`), else the
        nearest within ``max_km``; None when there is none."""
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        dlat = math.ceil(max_km / 111.0)
        coslat = max(math.cos(math.radians(min(abs(lat) + dlat, 90.0))), 1e-6)
        dlon = min(180, math.ceil(max_km / (111.32 * coslat)))
        clat, clon = math.floor(lat), math.floor(lon)
        best: tuple[float, Town] | None = None
        inside: tuple[float, Town] | None = None
        seen: set[int] = set()
        for a in range(clat - dlat, clat + dlat + 1):
            for b in range(clon - dlon, clon + dlon + 1):
                cell = (a, (b + 180) % 360 - 180)
                for i in self.cells.get(cell, ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    t = self.towns[i]
                    d = km_between(lat, lon, t.lat, t.lon)
                    if d <= max_km and (best is None or d < best[0]):
                        best = (d, t)
                    reach = t.reach_km
                    if reach > 0 and d <= reach and (inside is None or d / reach < inside[0]):
                        inside = (d / reach, t)
        if inside is not None:
            return inside[1]
        return best[1] if best else None

    @classmethod
    def from_files(cls, base: Path) -> "Gazetteer":
        """Read the three GeoNames files in ``base``."""
        return cls.parse(path_of(TOWNS, base).read_bytes(), path_of(REGIONS, base).read_text(encoding="utf-8"),
                         path_of(COUNTRIES, base).read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, towns_zip: bytes, regions_txt: str, countries_txt: str) -> "Gazetteer":
        countries: dict[str, str] = {}
        for line in countries_txt.splitlines():
            if not line or line.startswith("#"):
                continue
            f = line.split("\t")
            if len(f) > 4 and f[0]:
                countries[f[0]] = f[4]
        regions: dict[str, str] = {}
        for line in regions_txt.splitlines():
            f = line.split("\t")
            if len(f) >= 2 and f[0]:
                regions[f[0]] = f[1]
        out: list[Town] = []
        with zipfile.ZipFile(io.BytesIO(towns_zip)) as z:
            member = next(n for n in z.namelist() if n.endswith(".txt"))
            with z.open(member) as raw:
                for line in io.TextIOWrapper(raw, encoding="utf-8"):
                    f = line.rstrip("\n").split("\t")
                    if len(f) < 11 or f[7] in SKIP_CODES:
                        continue
                    try:
                        lat, lon = float(f[4]), float(f[5])
                    except ValueError:
                        continue
                    cc = f[8]
                    try:
                        pop = int(f[14]) if len(f) > 14 and f[14] else 0
                    except ValueError:
                        pop = 0
                    out.append(Town(name=f[1], region=regions.get(f"{cc}.{f[10]}", ""),
                                    country=countries.get(cc, cc), lat=lat, lon=lon, population=pop))
        return cls(out)


_table: Gazetteer | None = None
_table_lock = threading.Lock()


def table(base: Path | None = None) -> Gazetteer | None:
    """The town table, read once per process; None until it's here.
    Blocking the first time (a couple of seconds): call it in a thread."""
    global _table
    if _table is not None:
        return _table
    base = base or data_dir()
    if not ready(base):
        return None
    with _table_lock:
        if _table is None:
            _table = Gazetteer.from_files(base)
            log.info("places: %d towns loaded", len(_table))
    return _table


async def name_of(gps: dict[str, Any] | None) -> str | None:
    """The place name for a stored position: the town's label, '' when no
    town is within :data:`MAX_KM`, None when the table isn't here yet."""
    if not gps:
        return None
    tab = await asyncio.to_thread(table)
    if tab is None:
        return None
    town = tab.nearest(float(gps["lat"]), float(gps["lon"]))
    return town.label if town else ""


# ─── "at home" (inside the query: the house's coordinates stay out of rows) ─


def near_sql(alias: str = "m") -> str:
    """A SQL boolean: the item's GPS is within ``:home_km`` of
    ``(:home_lat, :home_lon)`` (bound by the caller, never stored)."""
    lat, lon = f"(({alias}.gps->>'lat')::float8)", f"(({alias}.gps->>'lon')::float8)"
    return (f"({alias}.gps IS NOT NULL AND 2 * {EARTH_KM} * asin(least(1.0, sqrt("
            f"power(sin(radians({lat} - :home_lat) / 2), 2) + cos(radians(:home_lat)) * cos(radians({lat})) "
            f"* power(sin(radians({lon} - :home_lon) / 2), 2)))) <= :home_km)")


def home_point(home: dict[str, Any] | None) -> tuple[float, float] | None:
    """The Home location's point, or None when none is set (or the name
    matched no town)."""
    if not home or home.get("lat") is None or home.get("lon") is None:
        return None
    return float(home["lat"]), float(home["lon"])


# ─── the switch and the data ─────────────────────────────────────────────


async def places_on_locked(s: Any) -> bool:
    """The switch, read under a share lock on its row: an admin's
    "locations off" (which writes that row, then deletes) waits for this
    transaction, or this one waits for it and sees locations off."""
    v = (await s.execute(text(
        "SELECT value FROM household_settings WHERE key = :k FOR SHARE"), {"k": switches.KEY})).scalar()
    return bool(switches.normalize(v)["places"])


async def read_state(s: Any) -> dict[str, Any]:
    v = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                         {"k": STATE_KEY})).scalar()
    if isinstance(v, str):
        v = json.loads(v)
    return dict(v) if isinstance(v, dict) else {}


async def write_state(s: Any, **changes: Any) -> dict[str, Any]:
    st = {**await read_state(s), **changes}
    await s.execute(text(
        "INSERT INTO household_settings (key, value) VALUES (:k, CAST(:v AS JSONB)) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": STATE_KEY, "v": json.dumps(st)})
    return st


async def located_count(s: Any) -> int:
    return int((await s.execute(text(
        "SELECT count(*) FROM media_items WHERE gps IS NOT NULL OR place_name IS NOT NULL"))).scalar() or 0)


async def delete_all(s: Any) -> int:
    """Locations off: every stored GPS position and place name goes, and
    those photos' search text is rebuilt without the place. The photo date
    stages forget they read locations, so turning it on again reads them
    afresh. Returns how many photos had one."""
    from domovoi.media_understanding import search

    ids = [int(r[0]) for r in (await s.execute(text(
        "UPDATE media_items SET gps = NULL, place_name = NULL, updated_at = now() "
        "WHERE gps IS NOT NULL OR place_name IS NOT NULL RETURNING id"))).all()]
    for i in ids:
        await search.refresh_doc(s, i)
    await s.execute(text(
        "UPDATE media_item_stages SET progress = progress - 'places' "
        "WHERE analyser = 'photo_date' AND progress ? 'places'"))
    await write_state(s, requeued=False, fetch_error=None)
    return len(ids)


async def requeue_photos(s: Any) -> int:
    """Photos dated while locations were off: their date stage runs again
    (cheap: the first 256 KB of the file) to read where they were taken."""
    ids = [int(r[0]) for r in (await s.execute(text(
        "UPDATE media_item_stages st SET state = 'pending', progress = '{}'::jsonb, attempts = 0, "
        "spent_s = 0, error_code = NULL, updated_at = now() FROM media_items m "
        "WHERE m.id = st.item_id AND m.kind = 'image' AND st.analyser = 'photo_date' "
        "AND st.state = 'done' AND coalesce(st.progress->>'places', 'false') <> 'true' "
        "RETURNING st.item_id"))).all()]
    if ids:
        await s.execute(text(
            "UPDATE media_items SET state = 'pending', attempts = 0, error_code = NULL, new_arrival = false, "
            "updated_at = now() WHERE id = ANY(:ids) AND state IN ('done', 'skipped')"), {"ids": ids})
    return len(ids)


#: Photos named per housekeeping step (positions kept before the table came).
NAME_BATCH = 500


async def name_waiting(s: Any) -> int:
    """Name the photos whose position was kept before the town list was
    here. '' marks "no town near", so none is looked at twice."""
    from domovoi.media_understanding import search

    rows = (await s.execute(text(
        "SELECT id, gps FROM media_items WHERE gps IS NOT NULL AND place_name IS NULL "
        "ORDER BY id LIMIT :n"), {"n": NAME_BATCH})).all()
    if not rows or not await places_on_locked(s):
        return 0
    n = 0
    for item_id, gps in rows:
        if isinstance(gps, str):
            gps = json.loads(gps)
        name = await name_of(gps)
        if name is None:
            return n
        await s.execute(text("UPDATE media_items SET place_name = :p WHERE id = :i AND gps IS NOT NULL"),
                        {"p": name, "i": item_id})
        await search.refresh_doc(s, int(item_id))
        n += 1
    return n


_fetch_task: asyncio.Task | None = None


async def _fetch_table() -> None:
    from domovoi import egress
    from domovoi.db.session import session_scope

    try:
        await asyncio.to_thread(download)
        err = None
    except egress.InternetTurnedOff:
        err = "internet access is turned off"
    except TableUnavailable as e:
        err = str(e)
    except Exception as e:  # noqa: BLE001
        err = type(e).__name__
    async with session_scope() as s:
        await write_state(s, fetch_error=err)


async def housekeeping(runner: Any) -> None:
    """Each library step: nothing while locations are off (never a fetch on
    a house that didn't opt in). On: fetch the town list once if it isn't
    here; when it is, queue the photos dated while off once, and name the
    positions kept before the list came."""
    global _fetch_task
    from domovoi.db.session import session_scope

    if not runner.sw.get("places"):
        return
    if not ready():
        if (_fetch_task is None or _fetch_task.done()) and may_retry():
            _fetch_task = asyncio.ensure_future(_fetch_table())
        return
    async with session_scope() as s:
        st = await read_state(s)
        if not st.get("requeued"):
            n = await requeue_photos(s)
            await write_state(s, requeued=True, fetch_error=None)
            log.info("places: %d photo%s queued to read where they were taken", n, "" if n == 1 else "s")
    async with session_scope() as s:
        await name_waiting(s)


def install() -> None:
    """Register the housekeeping with the runner (the photo date analyser
    does the per-photo work)."""
    from domovoi.media_understanding import runner

    runner.register_housekeeping("places", housekeeping)
