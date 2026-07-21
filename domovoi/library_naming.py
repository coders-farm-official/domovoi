"""Title parsing + Windows-strict filename sanitization for library media.

Core-owned (design §4.10, dossier §3 stay-list): the library indexer
needs the same "Artist - Title" parse the download pipeline uses, and
every path that writes a media file under ``MUSIC_DIR`` must sanitize
segments BEFORE the ``library_tracks`` insert (dossier §7 invariant 10
— Windows path duality) so the DB row and the on-disk file never
disagree about the name.
"""

from __future__ import annotations

import re
from pathlib import Path

# External uploads almost always title their media "Artist - Title
# (modifier)". Structured artist/track metadata is only present for
# official releases, so we fall back to parsing the title for everything
# else. Em-dash and en-dash variants both appear in real-world titles.
_ARTIST_TITLE_RE = re.compile(r"^(.+?)\s*[-–—]\s*(.+)$")
# Strip trailing parenthetical/bracket modifiers like "(Official Music
# Video)", "(Lyrics)", "[HD]" — descriptor cruft, not the song title.
_TITLE_TRAILING_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")

# Filesystem-illegal characters across Windows + POSIX. The host is
# Windows so we sanitize against the stricter set; the result is also
# POSIX-safe.
_ILLEGAL_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def parse_title_artist(title: str) -> tuple[str | None, str | None]:
    """Extract ``(artist, title)`` from an external media title.

    Returns ``(None, None)`` if no clear "Artist - Title" pattern is
    present. (The SDK's :meth:`LibraryAPI.parse_title_artist` wraps this
    in a ``(title, artist)`` order with a raw-title fallback.)
    """
    if not title:
        return None, None
    m = _ARTIST_TITLE_RE.match(title.strip())
    if not m:
        return None, None
    artist = m.group(1).strip()
    rest = m.group(2).strip()
    cleaned = _TITLE_TRAILING_RE.sub("", rest).strip() or rest
    return artist or None, cleaned or None


def sanitize_path_segment(s: str) -> str:
    """Remove characters that aren't legal in Windows filenames.

    Windows is the strictest target — ``< > : " / \\ | ? *`` plus
    control chars are forbidden, and trailing dots / spaces are silently
    stripped by the OS. POSIX would tolerate most of these but the
    Windows rules apply everywhere for cross-platform stability.
    """
    cleaned = _ILLEGAL_PATH_CHARS.sub("_", s).strip().rstrip(". ")
    return cleaned or "unknown"


def canonical_track_path(
    music_dir: str | Path, artist: str | None, title: str, ext: str = ".mp3"
) -> Path:
    """The canonical on-disk home for a library track:
    ``MUSIC_DIR/<safe artist>/<safe title><ext>`` — sanitized segments,
    ``unknown`` artist bucket when the artist is unresolved."""
    safe_artist = sanitize_path_segment(artist or "unknown")
    safe_title = sanitize_path_segment(title)
    return Path(music_dir) / safe_artist / f"{safe_title}{ext}"
