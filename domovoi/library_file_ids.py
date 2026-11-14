"""What a music file says about its own identity, credits and playlists.

Pure (no database, no I/O beyond the tags handed in). The indexer and the
tag backfill read these from every file (``TAGS_VERSION`` 2, V058); the
names are the ones MusicBrainz Picard writes and that other taggers and
downloaders follow, so a file another program already identified is
never fingerprinted again.

* **The recording id** (a MusicBrainz recording MBID): ID3 ``UFID`` with
  the owner ``http://musicbrainz.org``; Vorbis ``MUSICBRAINZ_TRACKID``
  (Picard's name for the recording id there); MP4
  ``----:com.apple.iTunes:MusicBrainz Track Id``; ASF
  ``MusicBrainz/Track Id``; APEv2 ``MUSICBRAINZ_TRACKID``.
* **The release (album) id**: ID3 ``TXXX:MusicBrainz Album Id``, Vorbis
  ``MUSICBRAINZ_ALBUMID``, MP4 ``...:MusicBrainz Album Id``, ASF
  ``MusicBrainz/Album Id``.
* **The release group id**: ``TXXX:MusicBrainz Release Group Id``, Vorbis
  ``MUSICBRAINZ_RELEASEGROUPID``, MP4 ``...:MusicBrainz Release Group Id``,
  ASF ``MusicBrainz/Release Group Id``.
* **Every credited artist**: ``TXXX:ARTISTS`` / Vorbis ``ARTISTS`` / MP4
  ``----:com.apple.iTunes:ARTISTS`` / ASF ``WM/ARTISTS``: one value per
  artist, or one value with the names separated by "; " (how some apps
  write it). Kept so "play songs by B" and the search find a song B is
  credited on even when the artist field names only A.
* **Playlists the file belongs to**: ``TXXX:PLAYLIST`` / Vorbis
  ``PLAYLIST`` / MP4 ``----:com.apple.iTunes:PLAYLIST``, the names
  separated by "; ". Imported as Domovoi playlists
  (``domovoi/library_playlists_import.py``).

Every id is checked to be a UUID (lower case) before it is kept; anything
else is ignored. Names are trimmed, NUL and control characters dropped,
each at most :data:`MAX_NAME_CHARS`, at most :data:`MAX_NAMES` per file.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

#: Most credited artists / playlist names kept per file.
MAX_NAMES = 16
#: Longest artist or playlist name kept.
MAX_NAME_CHARS = 200
#: Longest raw value looked at (a longer one is skipped whole).
MAX_RAW_CHARS = 4000

_MBID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MB_OWNER = "http://musicbrainz.org"

_MP4 = "----:com.apple.iTunes:"
RECORDING_KEYS = ("musicbrainz_trackid", f"{_MP4}MusicBrainz Track Id", "MusicBrainz/Track Id")
RELEASE_KEYS = (
    "TXXX:MusicBrainz Album Id", "musicbrainz_albumid", f"{_MP4}MusicBrainz Album Id",
    "MusicBrainz/Album Id",
)
RELEASE_GROUP_KEYS = (
    "TXXX:MusicBrainz Release Group Id", "musicbrainz_releasegroupid",
    f"{_MP4}MusicBrainz Release Group Id", "MusicBrainz/Release Group Id",
)
ARTISTS_KEYS = ("TXXX:ARTISTS", "artists", f"{_MP4}ARTISTS", "WM/ARTISTS")
PLAYLIST_KEYS = ("TXXX:PLAYLIST", "playlist", f"{_MP4}PLAYLIST")


def _texts(tags: Any, key: str) -> list[str]:
    """Every text value under ``key``: an ID3 TXXX frame's texts, an MP4
    freeform item's UTF-8 bytes, an ASF value's text, a Vorbis/APE text."""
    out: list[str] = []
    try:
        values = tags.get(key)
    except Exception:  # noqa: BLE001 — a key a format can't hold
        return out
    for value in values or ():
        if value is None:
            continue
        texts = getattr(value, "text", None)
        if isinstance(texts, list):
            out.extend(str(t) for t in texts)
        elif isinstance(value, (bytes, bytearray)):
            out.append(bytes(value).decode("utf-8", "replace"))
        else:
            out.append(str(value))
    return [t for t in out if t and len(t) <= MAX_RAW_CHARS]


def _mbid(text: str | None) -> str | None:
    t = (text or "").strip().strip("\x00").lower()
    return t if _MBID_RE.fullmatch(t) else None


def _first_mbid(tags: Any, keys: Iterable[str]) -> str | None:
    for key in keys:
        for value in _texts(tags, key):
            mbid = _mbid(value)
            if mbid:
                return mbid
    return None


def recording_id(tags: Any) -> str | None:
    """The MusicBrainz recording id a file carries, or None."""
    if tags is None:
        return None
    # ID3 UFID (owner "http://musicbrainz.org", then the id), read raw.
    getall = getattr(tags, "getall", None)
    if callable(getall):
        for frame in getall("UFID"):
            data = bytes(getattr(frame, "data", b"") or b"")
            owner, sep, ident = data.partition(b"\x00")
            if sep and owner.decode("latin-1", "replace").strip().lower() == _MB_OWNER:
                mbid = _mbid(ident.decode("latin-1", "replace"))
                if mbid:
                    return mbid
        # Some taggers also write TXXX:MusicBrainz Track Id (the recording).
        return _first_mbid(tags, ("TXXX:MusicBrainz Track Id",))
    return _first_mbid(tags, RECORDING_KEYS)


def release_id(tags: Any) -> str | None:
    return None if tags is None else _first_mbid(tags, RELEASE_KEYS)


def release_group_id(tags: Any) -> str | None:
    return None if tags is None else _first_mbid(tags, RELEASE_GROUP_KEYS)


def _clean_name(text: str) -> str:
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(cleaned.split())[:MAX_NAME_CHARS].strip()


def split_names(values: Iterable[str]) -> list[str]:
    """The names in ``values``: each value split on ";" (and on NUL, an
    ID3v2.4 list), trimmed, blanks dropped, each name once (case-blind),
    in first-seen order, at most :data:`MAX_NAMES`. Pure."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in re.split(r"[;\x00]", value or ""):
            name = _clean_name(part)
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            out.append(name)
            if len(out) >= MAX_NAMES:
                return out
    return out


def artist_credits(tags: Any) -> list[str]:
    """Every credited artist the file names (``ARTISTS``); [] when none."""
    if tags is None:
        return []
    for key in ARTISTS_KEYS:
        names = split_names(_texts(tags, key))
        if names:
            return names
    return []


def playlist_names(tags: Any) -> list[str]:
    """The playlists the file says it belongs to (``PLAYLIST``)."""
    if tags is None:
        return []
    for key in PLAYLIST_KEYS:
        names = split_names(_texts(tags, key))
        if names:
            return names
    return []


def read_ids(tags: Any) -> dict[str, Any]:
    """All of the above, as the indexer stores them (None / [] when absent)."""
    return {
        "mb_recording_id": recording_id(tags),
        "mb_release_id": release_id(tags),
        "mb_release_group_id": release_group_id(tags),
        "artist_credits": artist_credits(tags) or None,
        "file_playlists": playlist_names(tags) or None,
    }


__all__ = [
    "artist_credits",
    "playlist_names",
    "read_ids",
    "recording_id",
    "release_group_id",
    "release_id",
    "split_names",
]
