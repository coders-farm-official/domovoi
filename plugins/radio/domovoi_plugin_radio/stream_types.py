"""What a station URL answered with, and whether the plugin will use it.

Shared by the web process (the browser relay serves only audio, A2-02) and
the core process (the sampler feeds only an audio stream to ffmpeg, never a
playlist ffmpeg would open on its own, A2-03). Stdlib only, so both sides
may import it: the web side may not import ``domovoi.sdk``.
"""

from __future__ import annotations

# Audio that does not say ``audio/`` in its type.
_EXTRA_AUDIO_TYPES = frozenset({"application/ogg"})
# Types a stream server sends for audio it cannot name. Served as MP3: a
# media element sniffs the real codec, and ffmpeg probes it.
_GENERIC_TYPES = frozenset({"", "application/octet-stream"})
FALLBACK_AUDIO_TYPE = "audio/mpeg"

# A list of other URLs rather than a stream. Some of these are spelled
# ``audio/...``, so they are named here rather than caught by the rule.
PLAYLIST_TYPES = frozenset(
    {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "application/mpegurl",
        "audio/mpegurl",
        "audio/x-mpegurl",
        "audio/scpls",
        "audio/x-scpls",
        "application/pls+xml",
        "application/xspf+xml",
        "application/dash+xml",
        "audio/x-ms-asx",
        "video/x-ms-asf",
    }
)
# How a playlist begins, whatever type it was served under. No audio
# container starts with any of these.
_PLAYLIST_PREFIXES = (b"#EXTM3U", b"[playlist]", b"<?xml", b"<asx", b"<playlist")


def _base(declared: str | None) -> str:
    return (declared or "").split(";", 1)[0].strip().lower()


def _is_token(minor: str) -> bool:
    """A media subtype spelled the way RFC 9110 allows: ASCII letters,
    digits and ``+-.`` only. ``str.isalnum`` alone takes any Unicode
    letter, and a type such as ``audio/m\u0131`` then reached the
    response headers, which are latin-1: a 500, with the upstream left
    open (REV-B3 nit)."""
    return bool(minor) and all(c.isascii() and (c.isalnum() or c in "+-.") for c in minor)


def audio_media_type(declared: str | None) -> str | None:
    """The audio type to serve for an upstream that declared ``declared``,
    or None when it is not an audio STREAM.

    ``audio/*`` and ``application/ogg`` come back as declared (bare type,
    parameters dropped); a missing or ``application/octet-stream`` type
    becomes ``audio/mpeg``; everything else (``text/html``, scripts,
    images, XML, JSON, video, a subtype that is not plain ASCII) is None.
    So is a playlist, whatever its spelling (:data:`PLAYLIST_TYPES`):
    ``audio/x-mpegurl`` is the same HLS playlist as
    ``application/vnd.apple.mpegurl``, a list of other URLs rather than
    sound, and the relay and the sampler refuse every spelling alike."""
    base = _base(declared)
    if base in _GENERIC_TYPES:
        return FALLBACK_AUDIO_TYPE
    if base in PLAYLIST_TYPES:
        return None
    if base in _EXTRA_AUDIO_TYPES:
        return base
    major, _, minor = base.partition("/")
    if major == "audio" and _is_token(minor):
        return base
    return None


def is_playlist_type(declared: str | None) -> bool:
    """True for a playlist or manifest type (HLS, PLS, XSPF, DASH, ASX)."""
    return _base(declared) in PLAYLIST_TYPES


def looks_like_playlist(head: bytes) -> bool:
    """True when the first bytes of a body are a playlist's."""
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return any(stripped[: len(p)].lower() == p.lower() for p in _PLAYLIST_PREFIXES)


def samplable_audio_type(declared: str | None) -> bool:
    """True when a stream that declared ``declared`` may be handed to
    ffmpeg: audio by :func:`audio_media_type`, and not a playlist."""
    return audio_media_type(declared) is not None and not is_playlist_type(declared)
