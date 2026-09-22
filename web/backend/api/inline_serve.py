"""Serving a stored file to the browser without letting it become a page
of the dashboard (WEB-3).

The Files, Documents and Images surfaces hand back whatever is on disk.
Most of it is inert to a browser — a PDF, a JPEG, an MP3. Three families
are not: **HTML**, **SVG** and **XHTML** are documents that a browser
executes, and a browser that executes one served from
``http://<server>:6369`` runs it on the dashboard's own origin, next to
the dashboard's session cookie and its in-memory admin token.

So those three come back as a **download** rather than a rendering, under
two headers that hold even if something upstream disagrees about the
type:

* ``X-Content-Type-Options: nosniff`` — the browser must believe the
  declared type instead of guessing from the bytes (guessing is how a
  ``.txt`` full of markup becomes a page);
* ``Content-Security-Policy: sandbox`` — should the response be rendered
  anyway, it renders in an opaque origin: no scripts, no access to
  anything of ours.

Everything else keeps rendering inline, because that is the whole point
of "open this in a new tab".
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Media types a browser executes on our origin.
ACTIVE_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
    }
)

# Extensions that mean the same thing, checked as well as the media type:
# the type can arrive as ``application/octet-stream`` from a host whose
# mimetypes registry is thin (Windows), and the extension is what a
# browser looks at when a download is opened later.
ACTIVE_SUFFIXES: frozenset[str] = frozenset(
    {".html", ".htm", ".xhtml", ".xht", ".shtml", ".svg", ".svgz", ".xml"}
)

SANDBOX_CSP = "sandbox"
NOSNIFF = "nosniff"


def is_active_document(media_type: str | None, filename: str | None = None) -> bool:
    """True when a browser would treat these bytes as a document it runs."""
    base = (media_type or "").split(";", 1)[0].strip().lower()
    if base in ACTIVE_MEDIA_TYPES:
        return True
    if filename:
        return PurePosixPath(filename.replace("\\", "/")).suffix.lower() in ACTIVE_SUFFIXES
    return False


def disposition_for(media_type: str | None, filename: str | None = None) -> str:
    """``"attachment"`` for the executable families, ``"inline"`` for the
    rest — the argument ``FileResponse(content_disposition_type=...)``
    takes."""
    return "attachment" if is_active_document(media_type, filename) else "inline"


def inert_headers(media_type: str | None, filename: str | None = None) -> dict[str, str]:
    """Headers for a raw serve: always ``nosniff``, plus the sandbox CSP
    for anything a browser would execute."""
    headers = {"X-Content-Type-Options": NOSNIFF}
    if is_active_document(media_type, filename):
        headers["Content-Security-Policy"] = SANDBOX_CSP
    return headers
