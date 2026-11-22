"""Downloads for the kit builder, each held to its pin, all through the
outbound gate.

Every request goes through ``egress.sync_client`` (refused, redirects
included, under ``INTERNET_ACCESS=never``) and ``net_safety`` (public
addresses only). Nothing is kept that isn't exactly its pinned SHA-256
(and size, when the pin has one). Tests replace :func:`http_client`.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from domovoi import egress, net_safety

_CHUNK = 1 << 20

Tick = Callable[[int], None]


class FetchError(RuntimeError):
    pass


def http_client():
    import httpx

    return egress.sync_client(timeout=httpx.Timeout(30.0, read=300.0))


def fetch_file(url: str, dest: Path, *, sha256: str, size: int | None,
               progress: Tick | None = None, headers: dict[str, str] | None = None) -> Path:
    """Download ``url`` to ``dest``; kept only when it is ``sha256`` (and
    ``size`` bytes, when given). ``progress`` gets each chunk's length and
    may raise to stop. Raises FetchError (nothing partial is left)."""
    egress.require_destination(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.part")
    h = hashlib.sha256()
    written = 0
    try:
        with http_client() as client:
            response = net_safety.open_stream_sync(client, url, headers=headers)
            try:
                if response.status_code != 200:
                    raise FetchError(f"{url.split('/')[2]} answered HTTP {response.status_code}")
                with open(part, "wb") as f:
                    for chunk in response.iter_bytes(_CHUNK):
                        written += len(chunk)
                        if size is not None and written > size:
                            raise FetchError(f"{dest.name} is larger than its pinned {size} bytes")
                        h.update(chunk)
                        f.write(chunk)
                        if progress is not None:
                            progress(len(chunk))
            finally:
                response.close()
        if size is not None and written != size:
            raise FetchError(f"{dest.name}: {written} bytes, pinned {size}")
        if h.hexdigest() != sha256:
            raise FetchError(f"{dest.name} doesn't match its pin (SHA-256 {h.hexdigest()[:12]}…, "
                             f"pinned {sha256[:12]}…)")
        os.replace(part, dest)
        return dest
    finally:
        part.unlink(missing_ok=True)


def fetch_bytes(url: str, *, max_bytes: int, headers: dict[str, str] | None = None) -> bytes:
    """A small document (a manifest, an index), through the same gate."""
    egress.require_destination(url)
    with http_client() as client:
        r = net_safety.fetch_bytes_sync(url, max_bytes=max_bytes, headers=headers, client=client)
    if r.status_code != 200:
        raise FetchError(f"{url.split('/')[2]} answered HTTP {r.status_code}")
    return r.content


def sha256_file(path: Path, progress: Tick | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
            if progress is not None:
                progress(len(chunk))
    return h.hexdigest()


#: (path, size, mtime_ns, ino) -> SHA-256: a multi-GB file is hashed once
#: per change, not on every look at the card.
_DIGESTS: dict[tuple[str, int, int, int], str] = {}


def cached_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns, st.st_ino)
    hit = _DIGESTS.get(key)
    if hit is None:
        hit = _DIGESTS[key] = sha256_file(path)
    return hit


def matches(path: Path, sha256: str, size: int | None = None) -> bool:
    """``path`` is a plain file with this size (when given) and SHA-256."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if size is not None and path.stat().st_size != size:
            return False
        return cached_sha256(path) == sha256
    except OSError:
        return False


def present(path: Path, size: int | None = None) -> bool:
    """Cheap: a plain file of this size (for the card, before the build
    holds it to its SHA-256)."""
    try:
        return (not path.is_symlink() and path.is_file()
                and (size is None or path.stat().st_size == size))
    except OSError:
        return False
