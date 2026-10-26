"""Fonts API: the families the document and sheet editors can use, and
"More fonts", an admin adding a family for the whole house.

Routes:

* ``GET /api/fonts`` (device read, the Documents tier): every family the
  house has, bundled and added, with the URLs of its files.
* ``GET /api/fonts/catalogue`` (admin, Bearer): the open-licence families
  of the google/fonts repository an admin may add, built by the server and
  cached for a week under ``FONTS_DIR``.
* ``POST /api/fonts/fetch {family}`` (admin, Bearer): add one family.
* ``DELETE /api/fonts/{slug}`` (admin, Bearer): remove an added family.
* ``GET /fonts/{slug}/{file}`` (open, like the rest of the static tree):
  a font file or licence file, bundled (``web/static/fonts``) or added
  (``FONTS_DIR``).

**Licences.** Only the SIL Open Font License 1.1 and Apache-2.0
(:mod:`web.backend.fonts_store`). The catalogue lists only the
``ofl/`` and ``apache/`` folders, and only families whose METADATA.pb says
OFL or APACHE2; an add reads METADATA.pb again AND the licence file
beside the fonts, and refuses unless both agree. The licence file is
stored with the fonts and served beside them.

**Internet.** Everything here that leaves the house follows the
household's internet answer (:mod:`domovoi.egress`): under ``never`` the
catalogue and an add are refused with the shared 409 before anything is
asked, and the httpx client refuses non-local URLs on its own. Reading
and serving fonts never needs the internet.

**Files.** An added family's files are stored as fetched, unchanged (no
subsetting or conversion: browsers read TrueType and OpenType directly,
and an unchanged file keeps any Reserved Font Name usable). Each file is
capped at :data:`fonts_store.MAX_FILE_BYTES` and must start with a real
font signature; the family at :data:`fonts_store.MAX_FAMILY_BYTES`.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import egress
from domovoi.admin_auth import require_admin_mutation, require_device_read
from web.backend import fonts_store as fs
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(tags=["fonts"])

DAILY = [Depends(require_device_read)]
# Bearer only (the cookie never), with the pre-setup grace, for the
# catalogue GET too: building it makes the server reach GitHub, which a
# page that merely holds the dashboard cookie must not be able to start.
ADMIN = [Depends(require_admin_mutation)]

GITHUB_API = "https://api.github.com/repos/google/fonts"
RAW_BASE = "https://raw.githubusercontent.com/google/fonts"
CATALOGUE_FILE = "catalogue.json"
CATALOGUE_MAX_AGE_SEC = 7 * 24 * 3600
CATALOGUE_CONCURRENCY = 24
METADATA_MAX_BYTES = 512 * 1024
LICENCE_MAX_BYTES = 256 * 1024
TREE_MAX_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_SEC = 30.0
FILE_CACHE_CONTROL = "public, max-age=86400"
NOTIFY_CHANNEL = "fonts_changed"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_add_lock = asyncio.Lock()
_catalogue_lock = asyncio.Lock()
_catalogue_mem: dict[str, Any] | None = None
_manifest_cache: tuple[float, dict[str, Any]] | None = None

_MEDIA_TYPES = {
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".txt": "text/plain; charset=utf-8", "": "text/plain; charset=utf-8",
}


class FontsUnavailable(Exception):
    """GitHub could not be read (offline, rate limited, an error page)."""


# ─── the network (one function, so tests can stand in for GitHub) ───────


# One pooled client for a whole catalogue build or add (set by
# :func:`_session`): two thousand reads over one set of kept-alive TLS
# connections, rather than a handshake each.
_session_client: contextvars.ContextVar[Any] = contextvars.ContextVar("fonts_http", default=None)


@contextlib.asynccontextmanager
async def _session():
    if _session_client.get() is not None:
        yield
        return
    async with egress.async_client(
        timeout=HTTP_TIMEOUT_SEC, follow_redirects=True,
        limits=httpx.Limits(max_connections=CATALOGUE_CONCURRENCY,
                            max_keepalive_connections=CATALOGUE_CONCURRENCY),
    ) as client:
        token = _session_client.set(client)
        try:
            yield
        finally:
            _session_client.reset(token)


async def _http_get(url: str, max_bytes: int, accept: str | None = None) -> tuple[int, bytes]:
    """(status, body) for a GET, read up to ``max_bytes`` (more is an
    error, not a truncation). Goes through the egress client: under
    ``never`` it refuses before connecting."""
    headers = {"User-Agent": "Domovoi-fonts"}
    if accept:
        headers["Accept"] = accept
    async with _session():
        client = _session_client.get()
        async with client.stream("GET", url, headers=headers) as r:
            if r.status_code != 200:
                return r.status_code, b""
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise _TooLarge(int(declared))
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise _TooLarge(len(buf))
            return 200, bytes(buf)


class _QuietFontReads(logging.Filter):
    """httpx logs every request at INFO; a catalogue build is two thousand
    of them. Keep them out of the journal unless the operator runs at
    DEBUG (the same rule main.py applies to the snapshot poll)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
            return True
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        return "raw.githubusercontent.com/google/fonts/" not in msg


logging.getLogger("httpx").addFilter(_QuietFontReads())


class _TooLarge(Exception):
    def __init__(self, size: int) -> None:
        super().__init__(size)
        self.size = size


async def _get(url: str, max_bytes: int, accept: str | None = None) -> bytes | None:
    """The body, None for a 404, :class:`FontsUnavailable` for anything else."""
    try:
        status, body = await _http_get(url, max_bytes, accept)
    except egress.InternetTurnedOff:
        raise
    except _TooLarge:
        raise
    except Exception as e:  # noqa: BLE001 — offline, DNS, TLS, timeout
        log.info("fonts: GET %s failed: %s", url, e)
        raise FontsUnavailable(str(e)) from e
    if status == 404:
        return None
    if status != 200:
        raise FontsUnavailable(f"HTTP {status}")
    return body


def _refuse_if_offline(what: str) -> None:
    if egress.internet_turned_off():
        raise egress.http_exception(what)


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=502,
        detail="Couldn't reach the font collection on GitHub just now. Try again in a minute.",
    )


# ─── the catalogue ──────────────────────────────────────────────────────


async def _latest_commit() -> str:
    body = await _get(f"{GITHUB_API}/commits/main", 4096, accept="application/vnd.github.sha")
    sha = (body or b"").decode("ascii", "replace").strip()
    if not _SHA_RE.match(sha):
        raise FontsUnavailable("no commit")
    return sha


async def _tree(sha: str) -> list[dict[str, Any]]:
    body = await _get(f"{GITHUB_API}/git/trees/{sha}", TREE_MAX_BYTES)
    if body is None:
        raise FontsUnavailable("no tree")
    try:
        return list(json.loads(body).get("tree") or [])
    except ValueError as e:
        raise FontsUnavailable("bad tree") from e


async def _family_dirs(commit: str) -> list[str]:
    """``ofl/<slug>`` and ``apache/<slug>`` for every family folder at
    ``commit``; no other top-level folder is ever read."""
    root = await _tree(commit)
    out: list[str] = []
    for top in fs.ALLOWED_DIRS:
        node = next((t for t in root if t.get("path") == top and t.get("type") == "tree"), None)
        if node is None or not _SHA_RE.match(str(node.get("sha", ""))):
            continue
        for child in await _tree(node["sha"]):
            name = str(child.get("path", ""))
            if child.get("type") == "tree" and fs.valid_slug(name):
                out.append(f"{top}/{name}")
    return out


async def _metadata(commit: str, path: str) -> dict[str, Any] | None:
    body = await _get(f"{RAW_BASE}/{commit}/{path}/METADATA.pb", METADATA_MAX_BYTES)
    if body is None:
        return None
    return fs.parse_metadata(body.decode("utf-8", "replace"))


def _catalogue_path() -> Path:
    return fs.fonts_dir() / CATALOGUE_FILE


def _read_cached_catalogue() -> dict[str, Any] | None:
    global _catalogue_mem
    if _catalogue_mem is not None:
        return _catalogue_mem
    try:
        data = json.loads(_catalogue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not _SHA_RE.match(str(data.get("commit", ""))):
        return None
    _catalogue_mem = data
    return data


def _fresh(cat: dict[str, Any] | None) -> bool:
    return bool(cat) and time.time() - float(cat.get("built_at", 0)) < CATALOGUE_MAX_AGE_SEC


async def build_catalogue() -> dict[str, Any]:
    async with _session():
        return await _build_catalogue()


async def _build_catalogue() -> dict[str, Any]:
    """Read every family folder's METADATA.pb at the newest commit and keep
    the OFL and Apache ones. About two thousand small reads from
    raw.githubusercontent.com, 24 at a time: tens of seconds, once a week."""
    global _catalogue_mem
    commit = await _latest_commit()
    dirs = await _family_dirs(commit)
    sem = asyncio.Semaphore(CATALOGUE_CONCURRENCY)
    refused = 0

    async def one(path: str) -> dict[str, Any] | None:
        nonlocal refused
        async with sem:
            try:
                meta = await _metadata(commit, path)
            except (FontsUnavailable, _TooLarge):
                return None
        if not meta or not meta.get("name"):
            return None
        spdx = fs.ALLOWED_LICENCES.get(meta["license"].upper())
        if not spdx or not path.startswith(("ofl/", "apache/")):
            refused += 1
            return None
        return {
            "family": meta["name"], "slug": path.split("/", 1)[1], "path": path,
            "category": fs.category_of(meta), "licence": spdx,
            "variable": fs.is_variable(meta),
            "styles": sorted({f["style"] for f in meta["fonts"]}),
        }

    rows = [r for r in await asyncio.gather(*(one(p) for p in dirs)) if r]
    if not rows:
        raise FontsUnavailable("empty catalogue")
    rows.sort(key=lambda r: r["family"].lower())
    cat = {"commit": commit, "built_at": time.time(), "families": rows, "refused": refused}
    target = _catalogue_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(json.dumps(cat, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, target)
    except OSError as e:
        log.warning("fonts: could not keep the catalogue at %s: %s", target, e)
    _catalogue_mem = cat
    return cat


async def _catalogue(refresh: bool = False) -> dict[str, Any]:
    cat = _read_cached_catalogue()
    if _fresh(cat) and not refresh:
        return cat  # type: ignore[return-value]
    async with _catalogue_lock:
        cat = _read_cached_catalogue()
        if _fresh(cat) and not refresh:
            return cat  # type: ignore[return-value]
        return await build_catalogue()


# ─── the bundled set and the added rows ─────────────────────────────────


def _manifest() -> dict[str, Any]:
    global _manifest_cache
    try:
        mtime = fs.MANIFEST_PATH.stat().st_mtime
    except OSError:
        return {"families": []}
    if _manifest_cache is None or _manifest_cache[0] != mtime:
        _manifest_cache = (mtime, fs.bundled_manifest())
    return _manifest_cache[1]


def _weights(faces: list[dict[str, Any]]) -> list[int]:
    out: set[int] = set()
    for f in faces:
        lo, hi = (list(f.get("weight") or [400, 400]) + [400, 400])[:2]
        out.update([lo] if lo == hi else [w for w in range(100, 1000, 100) if lo <= w <= hi])
    return sorted(out)


def _public_faces(slug: str, faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for f in faces:
        face = {"url": f"/fonts/{slug}/{f['file']}", "style": f["style"],
                "weight": list(f["weight"]), "format": f["format"]}
        if f.get("unicode_range"):
            face["unicode_range"] = f["unicode_range"]
        out.append(face)
    return out


def _bundled_entries() -> list[dict[str, Any]]:
    out = []
    for f in _manifest().get("families") or []:
        out.append({
            "family": f["family"], "slug": f["slug"], "category": f["category"],
            "source": "bundled", "licence": f["licence"],
            "licence_url": f"/fonts/{f['slug']}/{f['licence_file']}",
            "weights": f.get("weights") or _weights(f["faces"]),
            "italic": bool(f.get("italic")),
            "faces": _public_faces(f["slug"], f["faces"]),
        })
    return out


async def _added_rows() -> list[dict[str, Any]]:
    """The ``fonts`` rows, [] when V041 has not run (a lane or box that is
    behind on migrations still lists the bundled set)."""
    try:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT family, slug, category, licence, licence_file, source_path, source_commit, "
                "faces, bytes, fetched_at FROM fonts ORDER BY lower(family)"
            ))).mappings().all()
    except Exception as e:  # noqa: BLE001 — table missing
        log.debug("fonts: no fonts table yet: %s", e)
        return []
    return [dict(r) for r in rows]


def _added_entry(r: dict[str, Any]) -> dict[str, Any]:
    faces = r["faces"] if isinstance(r["faces"], list) else json.loads(r["faces"] or "[]")
    fetched = r.get("fetched_at")
    return {
        "family": r["family"], "slug": r["slug"], "category": r["category"],
        "source": "fetched", "licence": r["licence"],
        "licence_url": f"/fonts/{r['slug']}/{r['licence_file']}",
        "weights": _weights(faces), "italic": any(f.get("style") == "italic" for f in faces),
        "faces": _public_faces(r["slug"], faces),
        "bytes": int(r.get("bytes") or 0),
        "fetched_at": fetched.isoformat() if isinstance(fetched, datetime) else fetched,
        "from": f"google/fonts {r['source_path']}",
    }


async def _notify(slug: str) -> None:
    try:
        async with session_scope() as s:
            await s.execute(text("SELECT pg_notify(:c, :p)"), {"c": NOTIFY_CHANNEL, "p": slug})
    except Exception as e:  # noqa: BLE001 — the poll loop catches up
        log.debug("fonts: notify failed: %s", e)


async def snapshot() -> list[dict[str, Any]]:
    """The ``fonts`` realtime channel: the added families' slugs and when
    each was added, so an editor on screen hears of a new one."""
    return [{"slug": r["slug"], "fetched_at": _added_entry(r)["fetched_at"]}
            for r in await _added_rows()]


# ─── routes ─────────────────────────────────────────────────────────────


@router.get("/api/fonts", dependencies=DAILY)
async def list_fonts() -> dict[str, Any]:
    """Every family the editors can use: the bundled set (``source:
    "bundled"``) and the ones an admin added (``"fetched"``), each with its
    faces' URLs. ``internet_off`` tells an admin page why "More fonts" is
    greyed. ``css`` is the bundled set's @font-face sheet."""
    added = [_added_entry(r) for r in await _added_rows()]
    families = _bundled_entries() + added
    families.sort(key=lambda f: f["family"].lower())
    return {
        "families": families,
        "css": "/fonts/fonts.css",
        "internet_off": egress.internet_turned_off(),
        "categories": list(fs.CATEGORY_ORDER),
    }


@router.get("/api/fonts/catalogue", dependencies=ADMIN)
async def catalogue(refresh: bool = False) -> dict[str, Any]:
    """The open-licence families an admin can add (OFL and Apache only),
    each marked ``installed`` when the house already has it."""
    _refuse_if_offline("font catalogue")
    try:
        cat = await _catalogue(refresh=refresh)
    except FontsUnavailable:
        raise _unavailable()
    except egress.InternetTurnedOff:
        raise egress.http_exception("font catalogue")
    have = fs.bundled_slugs(_manifest()) | {r["slug"] for r in await _added_rows()}
    return {
        "commit": cat["commit"],
        "built_at": datetime.fromtimestamp(float(cat["built_at"]), timezone.utc).isoformat(),
        "families": [{**f, "installed": f["slug"] in have} for f in cat["families"]],
    }


LOOKUP_MAX = 12


@router.get("/api/fonts/lookup", dependencies=ADMIN)
async def lookup(family: list[str] = Query(default=[])) -> dict[str, Any]:
    """Can these families be added? For the picker's "not installed" rows:
    an admin is offered "add for the house" only for a family the
    open-licence collection has (no button that can only fail). Answers
    from the cached catalogue when there is one, else reads each family's
    METADATA.pb (``ofl/`` then ``apache/``). ``{families: {<asked>:
    {available, family?, licence?}}}``; at most 12 names a call."""
    _refuse_if_offline("font lookup")
    names = [n.strip() for n in family if n and n.strip()][:LOOKUP_MAX]
    out: dict[str, Any] = {}
    cat = _read_cached_catalogue()
    try:
        async with _session():
            commit: str | None = None
            for name in names:
                hit = _find_in_catalogue(name) if cat else None
                if hit is None and not cat:
                    slug = fs.slugify(name)
                    if fs.valid_slug(slug):
                        commit = commit or await _latest_commit()
                        for top in fs.ALLOWED_DIRS:
                            meta = await _metadata(commit, f"{top}/{slug}")
                            spdx = fs.ALLOWED_LICENCES.get((meta or {}).get("license", "").upper())
                            if meta and spdx:
                                hit = {"family": meta["name"], "licence": spdx}
                                break
                out[name] = ({"available": True, "family": hit["family"], "licence": hit["licence"]}
                             if hit else {"available": False})
    except FontsUnavailable:
        raise _unavailable()
    except egress.InternetTurnedOff:
        raise egress.http_exception("font lookup")
    return {"families": out}


class FetchBody(BaseModel):
    family: str = Field(..., min_length=1, max_length=100)


def _find_in_catalogue(family: str) -> dict[str, Any] | None:
    cat = _read_cached_catalogue()
    if not cat:
        return None
    want = family.strip().lower()
    slug = fs.slugify(family)
    for f in cat["families"]:
        if f["family"].lower() == want or f["slug"] == slug:
            return f
    return None


async def _locate(family: str, commit: str) -> tuple[str, dict[str, Any]]:
    """(``ofl/<slug>`` or ``apache/<slug>``, METADATA) for a family name,
    or a 404/422 that says why not."""
    hit = _find_in_catalogue(family)
    slug = hit["slug"] if hit else fs.slugify(family)
    if not fs.valid_slug(slug):
        raise HTTPException(status_code=404, detail="There's no font family by that name.")
    for top in fs.ALLOWED_DIRS:
        meta = await _metadata(commit, f"{top}/{slug}")
        if meta is not None:
            return f"{top}/{slug}", meta
    # Not under an allowed licence folder. Say why when the collection has
    # it under another licence (only its METADATA is read, nothing else).
    for top in ("ufl", "cc-by-sa"):
        meta = await _metadata(commit, f"{top}/{slug}")
        if meta is not None:
            _, why = fs.licence_verdict(meta.get("license") or top.upper(), None)
            raise HTTPException(status_code=422, detail=f"Can't add {meta.get('name') or family}: {why}.")
    raise HTTPException(status_code=404, detail="There's no font family by that name in the open-licence collection.")


@router.post("/api/fonts/fetch", dependencies=ADMIN)
async def fetch_family(body: FetchBody) -> dict[str, Any]:
    """Add one family for the whole house: check its licence, download its
    files, store them with the licence file, record it."""
    family = body.family.strip()
    _refuse_if_offline("font download")
    slug_guess = fs.slugify(family)
    if slug_guess in fs.bundled_slugs(_manifest()) or family.lower() in fs.bundled_names(_manifest()):
        raise HTTPException(status_code=409, detail=f"{family} already comes with Domovoi.")
    async with _add_lock, _session():
        try:
            cat = _read_cached_catalogue()
            commit = cat["commit"] if cat else await _latest_commit()
            path, meta = await _locate(family, commit)
            slug = path.split("/", 1)[1]
            if slug in fs.bundled_slugs(_manifest()):
                raise HTTPException(status_code=409, detail=f"{meta['name']} already comes with Domovoi.")
            existing = {r["slug"]: r for r in await _added_rows()}
            if slug in existing:
                return {"added": False, "family": _added_entry(existing[slug])}
            entry = await _download(commit, path, meta)
        except FontsUnavailable:
            raise _unavailable()
        except egress.InternetTurnedOff:
            raise egress.http_exception("font download")
        except _TooLarge:
            raise HTTPException(status_code=422, detail="That font family is too large to add.")
    await _notify(entry["slug"])
    return {"added": True, "family": entry}


async def _download(commit: str, path: str, meta: dict[str, Any]) -> dict[str, Any]:
    slug = path.split("/", 1)[1]
    name = meta.get("name") or slug
    licence_name = fs.LICENCE_FILES.get((meta.get("license") or "").upper())
    licence_text = None
    if licence_name:
        raw = await _get(f"{RAW_BASE}/{commit}/{path}/{licence_name}", LICENCE_MAX_BYTES)
        licence_text = raw.decode("utf-8", "replace") if raw is not None else None
    spdx, why = fs.licence_verdict(meta.get("license") or "", licence_text)
    if not spdx:
        raise HTTPException(status_code=422, detail=f"Can't add {name}: {why}.")
    picks = fs.pick_files(meta)
    if not picks:
        raise HTTPException(status_code=422, detail=f"Can't add {name}: it has no font files.")
    root = fs.fonts_dir()
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".adding-{slug}-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        (staging / licence_name).write_bytes(licence_text.encode("utf-8"))
        faces: list[dict[str, Any]] = []
        total = 0
        for pick in picks:
            fname = pick["filename"]
            if not fs.valid_filename(fname):
                raise HTTPException(status_code=422, detail=f"Can't add {name}: a file has an odd name.")
            data = await _get(f"{RAW_BASE}/{commit}/{path}/{_quote(fname)}", fs.MAX_FILE_BYTES)
            if data is None:
                raise HTTPException(status_code=422, detail=f"Can't add {name}: a font file is missing.")
            fmt = fs.sniff_font(data)
            if fmt is None:
                raise HTTPException(status_code=422, detail=f"Can't add {name}: a file is not a font.")
            total += len(data)
            if total > fs.MAX_FAMILY_BYTES:
                raise HTTPException(status_code=422, detail="That font family is too large to add.")
            lo, hi = fs.weight_range(meta, pick)
            ext = {"ttf": ".ttf", "otf": ".otf", "woff2": ".woff2", "woff": ".woff"}[fmt]
            stored = f"{slug}-{pick['style']}-{lo}" + (f"-{hi}" if hi != lo else "") + ext
            (staging / stored).write_bytes(data)
            faces.append({"file": stored, "style": pick["style"], "weight": [lo, hi],
                          "format": fmt, "bytes": len(data), "from": fname})
        final = root / slug
        if final.exists():
            shutil.rmtree(final)  # a leftover folder with no row
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    row = {
        "family": name, "slug": slug, "category": fs.category_of(meta), "licence": spdx,
        "licence_file": licence_name, "source_path": path, "source_commit": commit,
        "faces": faces, "bytes": total,
    }
    try:
        async with session_scope() as s:
            got = (await s.execute(text(
                "INSERT INTO fonts (family, slug, category, licence, licence_file, source_path, "
                "source_commit, faces, bytes) VALUES (:family, :slug, :category, :licence, "
                ":licence_file, :source_path, :source_commit, CAST(:faces AS jsonb), :bytes) "
                "RETURNING family, slug, category, licence, licence_file, source_path, "
                "source_commit, faces, bytes, fetched_at"
            ), {**row, "faces": json.dumps(faces)})).mappings().one()
    except Exception:
        shutil.rmtree(root / slug, ignore_errors=True)
        raise
    log.info("fonts: added %s (%s, %s, %d bytes)", name, path, spdx, total)
    return _added_entry(dict(got))


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")


@router.delete("/api/fonts/{slug}", dependencies=ADMIN)
async def remove_family(slug: str) -> dict[str, Any]:
    """Remove an added family: its row and its folder. Documents that use
    it show it in a fallback face, marked "not installed", until it is
    added again. The bundled set can't be removed."""
    if slug in fs.bundled_slugs(_manifest()):
        raise HTTPException(status_code=409, detail="That font comes with Domovoi and can't be removed.")
    if not fs.valid_slug(slug):
        raise HTTPException(status_code=404, detail="No such font.")
    async with session_scope() as s:
        gone = (await s.execute(text("DELETE FROM fonts WHERE slug = :s RETURNING family"),
                                {"s": slug})).first()
    if gone is None:
        raise HTTPException(status_code=404, detail="No such font.")
    shutil.rmtree(fs.fonts_dir() / slug, ignore_errors=True)
    await _notify(slug)
    return {"removed": gone[0]}


@router.get("/fonts/{slug}/{name}", include_in_schema=False)
async def font_file(slug: str, name: str) -> Response:
    """One font or licence file. The bundled tree first, then the added
    families' folder; nothing outside either is ever reached (both names
    are checked against fixed patterns before a path is built)."""
    if not fs.valid_slug(slug) or not fs.valid_filename(name):
        raise HTTPException(status_code=404)
    for base in (fs.BUNDLED_DIR, fs.fonts_dir()):
        path = base / slug / name
        if path.is_file():
            ext = Path(name).suffix.lower()
            media = _MEDIA_TYPES.get(ext)
            if media is None:
                break
            return FileResponse(path, media_type=media,
                                headers={"Cache-Control": FILE_CACHE_CONTROL})
    raise HTTPException(status_code=404)
