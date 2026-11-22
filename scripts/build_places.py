"""Build the bundled town list for Home location's type-ahead.

    python scripts/build_places.py            # writes domovoi/places/data/cities15000.tsv.gz
    python scripts/build_places.py --check    # rebuilds in memory, compares with the file

Source: GeoNames (geonames.org), CC BY 4.0. ``cities15000`` is every town
with more than 15,000 people plus the capitals; ``admin1CodesASCII`` names
their states and regions. GeoNames republishes both files daily, so the
copies used are Internet Archive snapshots, pinned by URL and SHA-256: the
same inputs give the same output, byte for byte.

The output is a gzip'd, tab-separated table (header row first), sorted by
population, largest first:

    name  ascii_name  admin1  country  lat  lon  population

Only the server reads it (domovoi/places.py). Nothing is fetched at run time.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "domovoi" / "places" / "data" / "cities15000.tsv.gz"

SOURCES = {
    "cities15000.zip": (
        "https://web.archive.org/web/20261110090942id_/"
        "https://download.geonames.org/export/dump/cities15000.zip",
        "0df068fe1eb445f18655559b9d80fdd45ac88535d8c27b7b4f5ce5e66b1107b3",
    ),
    "admin1CodesASCII.txt": (
        "https://web.archive.org/web/20261117201220id_/"
        "https://download.geonames.org/export/dump/admin1CodesASCII.txt",
        "2e4867f5397acb056ea26377601b57af37cb4c81399770225018e9e439e91ad3",
    ),
}

HEADER = ("name", "ascii_name", "admin1", "country", "lat", "lon", "population")


def fetch(name: str) -> bytes:
    url, sha = SOURCES[name]
    req = urllib.request.Request(url, headers={"User-Agent": "domovoi-build-places"})
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (pinned https URL)
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        sys.exit(f"{name}: sha256 {got} is not the pinned {sha}")
    return data


def _clean(s: str) -> str:
    return s.replace("\t", " ").replace("\n", " ").strip()


def build(cities_zip: bytes, admin1_txt: bytes) -> bytes:
    admin1: dict[str, str] = {}
    for line in admin1_txt.decode("utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            admin1[cols[0]] = _clean(cols[1])

    with zipfile.ZipFile(io.BytesIO(cities_zip)) as z:
        text = z.read("cities15000.txt").decode("utf-8")

    rows = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) < 15:
            continue
        name, ascii_name, country = _clean(c[1]), _clean(c[2]), c[8].strip().upper()
        if not name or len(country) != 2:
            continue
        rows.append((
            name, ascii_name or name, admin1.get(f"{country}.{c[10].strip()}", ""), country,
            f"{float(c[4]):.4f}", f"{float(c[5]):.4f}", str(int(c[14] or 0)),
            int(c[0]),
        ))
    # Largest first; geonameid breaks ties so the order never depends on input order.
    rows.sort(key=lambda r: (-int(r[6]), r[7]))

    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar="\\")
    w.writerow(HEADER)
    for r in rows:
        w.writerow(r[:7])
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, compresslevel=9, mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="compare a fresh build with the committed file")
    args = ap.parse_args()
    data = build(fetch("cities15000.zip"), fetch("admin1CodesASCII.txt"))
    if args.check:
        same = OUT.exists() and OUT.read_bytes() == data
        print("up to date" if same else f"{OUT} differs from a fresh build")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)
    print(f"wrote {OUT.relative_to(REPO)}: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
