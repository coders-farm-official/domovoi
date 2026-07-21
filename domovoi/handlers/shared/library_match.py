"""Resolve an MPD-reported relative path to its ``library_tracks`` row.

The :mod:`domovoi.workers.library_indexer` walks
``settings.music_dir`` via :py:meth:`pathlib.Path.rglob` and stamps
``str(path)`` — on Windows that produces a backslash-separated
absolute path like ``C:\\Users\\Kamron\\music\\Artist\\Track.mp3``.
MPD inside Docker mounts the same directory at ``/music`` and
reports forward-slash relative URIs like ``Artist/Track.mp3``.

Joining ``Path(settings.music_dir).expanduser() / mpd_file`` and
string-ifying it reproduces what the indexer stored. Exact-string
match against ``library_tracks.file_path``; no ``LIKE`` escape
rules in play.

Used by:

* :func:`web.backend.api.music.favorite_now_playing` (the
  now-playing heart and the heart's filled-state lookup).
* :class:`domovoi.handlers.playlist.PlaylistHandler` (the
  voice "add this to my X playlist" path, which needs to resolve
  the now-playing local file to a library row before inserting).
"""

from __future__ import annotations

from pathlib import Path


def library_path_for_mpd_file(mpd_file: str) -> str:
    """Return the absolute host path the library indexer would have
    stamped for an MPD-relative URI.

    Imports ``domovoi.config.settings`` lazily so this module
    is safe to import from places that haven't initialized config
    yet (e.g. test bootstrap).
    """
    from domovoi.config import settings

    return str(Path(settings.music_dir).expanduser() / mpd_file)
