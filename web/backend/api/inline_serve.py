"""Serving a stored file to the browser without letting it become a page
of the dashboard (WEB-3, WEB-10).

The Files, Documents and Images surfaces hand back whatever is on disk,
from ``http://<server>:6369`` — the dashboard's own origin, next to the
dashboard's session cookie and its in-memory admin token. A file the
browser treats as a DOCUMENT runs there.

This used to be a denylist: HTML, XHTML, SVG and two XML types were
downloaded, everything else rendered inline. That is the wrong way round,
because the set of types a browser will parse as a document is open-ended:
every ``*/*+xml`` type is XML to a browser (``.rss``, ``.atom``, ``.xsl``,
``.rdf``, ``.kml``, ``.xaml`` …), XML honours XHTML-namespaced scripts and
``<?xml-stylesheet?>``, and which of those a host's ``mimetypes`` registry
knows about differs between Debian, Windows and whatever comes next. So
it is an ALLOWLIST now (WEB-10): a short list of types a browser renders
inertly — raster pictures, audio, video, PDF and plain text — opens in the
tab; **everything else** comes back as a download under two headers that
hold even if something upstream disagrees about the type:

* ``X-Content-Type-Options: nosniff`` — the browser must believe the
  declared type instead of guessing from the bytes (guessing is how a
  ``.txt`` full of markup becomes a page);
* ``Content-Security-Policy: sandbox`` — should the response be rendered
  anyway, it renders in an opaque origin: no scripts, no access to
  anything of ours.

What renders inline is what "open this in a new tab" exists for: a PDF,
a photo, a song, a video, a text file.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Raster pictures a browser decodes and never executes. SVG is NOT here:
# it is a document (scripts, foreignObject, event handlers).
INLINE_IMAGE_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/pjpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/x-ms-bmp",
        "image/avif",
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/tiff",
        "image/heic",
        "image/heif",
    }
)

# Exact types, besides the families below, that render inline.
INLINE_MEDIA_TYPES: frozenset[str] = INLINE_IMAGE_TYPES | frozenset(
    {"application/pdf", "text/plain"}
)

# Whole families a browser plays rather than parses: ``audio/*`` and
# ``video/*``. A ``+xml`` subtype in either is still refused (below).
INLINE_FAMILIES: tuple[str, ...] = ("audio/", "video/")

# Media types a browser executes on our origin. No longer what decides
# (the allowlist above does); kept because callers and tests name them,
# and because they are refused inline whatever the allowlist says.
ACTIVE_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
    }
)

# Extensions that are documents whatever type the host's registry gave
# them: the type can arrive as ``text/plain`` or ``application/octet-
# stream`` from a host whose mimetypes registry is thin (Windows), and the
# extension is what a browser looks at when a download is opened later. A
# name with one of these never renders inline, even under an allowed type.
ACTIVE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".html", ".htm", ".xhtml", ".xht", ".shtml", ".svg", ".svgz", ".xml",
        ".rss", ".atom", ".xsl", ".xslt", ".rdf", ".kml", ".gpx", ".mathml",
        ".mml", ".smil", ".smi", ".xspf", ".xul", ".xaml", ".mht", ".mhtml",
        ".hta", ".xbl", ".owl", ".wsdl", ".xsd", ".dtd", ".js", ".mjs",
    }
)

SANDBOX_CSP = "sandbox"
NOSNIFF = "nosniff"


def _base_type(media_type: str | None) -> str:
    return (media_type or "").split(";", 1)[0].strip().lower()


def renders_inline(media_type: str | None, filename: str | None = None) -> bool:
    """True only for the short list of types a browser shows without
    running anything: raster images, ``audio/*``, ``video/*``, PDF and
    plain text — and only when the file's name is not a document's."""
    base = _base_type(media_type)
    if not base or "xml" in base or base in ACTIVE_MEDIA_TYPES:
        return False
    if filename and (
        PurePosixPath(filename.replace("\\", "/")).suffix.lower() in ACTIVE_SUFFIXES
    ):
        return False
    if base in INLINE_MEDIA_TYPES:
        return True
    return base.startswith(INLINE_FAMILIES)


def is_active_document(media_type: str | None, filename: str | None = None) -> bool:
    """True when these bytes must NOT render on our origin — which is now
    everything :func:`renders_inline` does not vouch for."""
    return not renders_inline(media_type, filename)


def disposition_for(media_type: str | None, filename: str | None = None) -> str:
    """``"inline"`` for the allowlisted types, ``"attachment"`` for the
    rest — the argument ``FileResponse(content_disposition_type=...)``
    takes."""
    return "inline" if renders_inline(media_type, filename) else "attachment"


def inert_headers(media_type: str | None, filename: str | None = None) -> dict[str, str]:
    """Headers for a raw serve: always ``nosniff``, plus the sandbox CSP
    for anything that is not on the inline allowlist."""
    headers = {"X-Content-Type-Options": NOSNIFF}
    if not renders_inline(media_type, filename):
        headers["Content-Security-Policy"] = SANDBOX_CSP
    return headers
