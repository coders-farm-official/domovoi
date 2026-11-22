"""Towns and cities, offline: the type-ahead behind Settings → Household →
Home location, and the matcher the one-time carry-over uses.

The list is GeoNames' ``cities15000`` (every place over 15,000 people, plus
capitals) with ``admin1CodesASCII`` for state and region names, trimmed and
gzip'd into ``data/cities15000.tsv.gz`` by ``scripts/build_places.py``.
GeoNames data is CC BY 4.0 (geonames.org); docs/LICENSES.md and the
Home location card carry the attribution. Everything is read from that
file: nothing leaves the house.

A place is ``{"name": "Denver, Colorado, US", "lat": 39.7392,
"lon": -104.9847, "country": "US"}``.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parent / "data" / "cities15000.tsv.gz"
HEADER = ("name", "ascii_name", "admin1", "country", "lat", "lon", "population")
MAX_LIMIT = 20

# A carried-over name with several towns of that name is matched to the
# biggest only when it is this many times the next one ("Portland" is
# Oregon; "Springfield" stays a question).
CLEAR_LEAD = 10

_NOT_WORD = re.compile(r"[^0-9a-z]+")


def fold(text: str) -> str:
    """Lower case, accents and punctuation gone, spaces single:
    ``"Zürich-Altstetten"`` → ``"zurich altstetten"``."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = s.replace("ß", "ss").replace("ø", "o").replace("đ", "d").replace("ł", "l").replace("ı", "i")
    return _NOT_WORD.sub(" ", s).strip()


@dataclass(frozen=True)
class City:
    name: str
    admin1: str
    country: str
    lat: float
    lon: float
    population: int
    keys: tuple[str, ...]      # folded name and ascii name
    label_key: str             # folded "name admin1 country"

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin1 and fold(self.admin1) != fold(self.name):
            parts.append(self.admin1)
        parts.append(self.country)
        return ", ".join(parts)

    def place(self) -> dict[str, Any]:
        return {"name": self.label, "lat": self.lat, "lon": self.lon, "country": self.country}


@lru_cache(maxsize=1)
def cities() -> tuple[City, ...]:
    """Every town, biggest first (the file's order)."""
    out = []
    with gzip.open(DATA_FILE, "rt", encoding="utf-8") as f:
        header = tuple(next(f).rstrip("\n").split("\t"))
        if header != HEADER:
            raise ValueError(f"{DATA_FILE.name}: unexpected header {header}")
        for line in f:
            name, ascii_name, admin1, country, lat, lon, pop = line.rstrip("\n").split("\t")
            keys = tuple(dict.fromkeys(k for k in (fold(name), fold(ascii_name)) if k))
            out.append(City(name, admin1, country, float(lat), float(lon), int(pop), keys,
                            fold(f"{name} {admin1} {country}")))
    return tuple(out)


def suggest(text: str, limit: int = 8) -> list[dict[str, Any]]:
    """Towns whose name starts with ``text`` (accents and case ignored),
    biggest first. ``"denver, co"`` also narrows by state or country."""
    q = fold(text)
    limit = max(1, min(int(limit or 8), MAX_LIMIT))
    if not q:
        return []
    out, seen = [], set()
    for c in cities():
        if any(k.startswith(q) for k in c.keys) or c.label_key.startswith(q):
            label = c.label
            if label in seen:
                continue
            seen.add(label)
            out.append(c.place())
            if len(out) >= limit:
                break
    return out


def match(text: str) -> dict[str, Any] | None:
    """The one town ``text`` clearly names, or None.

    The first comma part must be a town's whole name; any further parts
    must each start its state/region or its country code. Several towns
    left: the biggest, only when it is CLEAR_LEAD times the next."""
    parts = [fold(p) for p in str(text or "").split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return None
    town, rest = parts[0], parts[1:]
    found = []
    for c in cities():
        if town not in c.keys and fold(c.label) != " ".join(parts):
            continue
        where = (fold(c.admin1), c.country.lower())
        if all(any(w.startswith(r) for w in where if w) for r in rest):
            found.append(c)
    if not found:
        return None
    if len(found) > 1 and found[0].population < CLEAR_LEAD * max(found[1].population, 1):
        return None
    return found[0].place()
