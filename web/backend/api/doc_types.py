"""Which files are documents: the line between the Documents and Other views.

Documents and Other are two views of ONE shared folder (``documents_dir``),
decided by Kamron on 2026-10-27: "Only doc related file types should show in
docs, then everything can show in other but the other tab only allows for
downloading/uploading", then "Everything out of the shared Documents
directory", then "whatever shows in Documents must not show in Other".

* **Documents** lists the document types below, plus folders, and keeps every
  editor, viewer, Chat attach and print action.
* **Other** (library id ``core:other``) lists everything else in the same
  folder, plus folders. Its files are only downloaded, renamed, moved or
  deleted: nothing reads their contents.

Nothing is moved on disk and nothing vanishes: a file that is not a document
simply shows in Other instead of Documents. A write follows the same line: a
new file whose type belongs to the other view is refused with a sentence that
names the right place (:func:`view_refusal`). Folders belong to both.

The document types are the classification sets ``documents.py`` already uses
for its editors and readers: text and markdown (``_TEXT_EXTS``, which includes
``.html``/``.htm``, the formatted documents' own format), Word/ODT/RTF,
spreadsheets, drawings (``.excalidraw`` and their ``.svg`` exports) and PDF.

Pictures are NOT documents. Nothing in the product keeps pictures in
Documents: the formatted-document editor embeds pictures in the ``.html`` as
``data:image`` URIs (a relative ``<img src>`` is stripped by both sanitisers),
scans are saved as PDFs, chat drafts as ``.docx``/``.pdf`` and drawings as
``.excalidraw``/``.svg``. A picture already in the folder shows in Other.
"""

from __future__ import annotations

import os
from typing import Optional

from web.backend.api.audio_serve import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from web.backend.api.documents import (
    _DOC_EXTS,
    _DRAWING_EXTS,
    _IMAGE_EXTS,
    _MARKDOWN_EXTS,
    _OFFICE_WP_EXTS,
    _SHEET_EXTS,
    _TEXT_EXTS,
)

#: Every extension the Documents view lists and accepts.
DOCUMENT_EXTS: frozenset[str] = (
    _TEXT_EXTS | _DOC_EXTS | _MARKDOWN_EXTS | _OFFICE_WP_EXTS | _SHEET_EXTS
    | _DRAWING_EXTS | frozenset({".pdf", ".html", ".htm"})
)

# Pictures a phone or camera makes that ``_IMAGE_EXTS`` (the types a browser
# opens) leaves out; only used to name Pictures in a refusal.
_PICTURE_EXTS = (_IMAGE_EXTS - _DRAWING_EXTS) | frozenset(
    {".heic", ".heif", ".tif", ".tiff", ".jxl", ".raw", ".dng", ".cr2", ".nef", ".arw"}
)

#: The two views of the Documents folder (``MediaLibrary.view``).
VIEW_DOCUMENTS = "documents"
VIEW_OTHER = "other"

OTHER_REFUSAL = (
    "Documents shows documents, spreadsheets and PDFs. Upload this in Other; "
    "it's the same shared folder."
)
DOCUMENT_IN_OTHER_REFUSAL = "This is a document. Upload it in Documents."


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def is_document_name(name: str) -> bool:
    """True when a file of this name belongs to the Documents view."""
    return ext_of(name) in DOCUMENT_EXTS


def in_view(view: Optional[str], name: str, is_dir: bool) -> bool:
    """Whether a listing of ``view`` shows this entry. Folders show in both;
    a library with no view (every other library) shows everything."""
    if is_dir or view is None:
        return True
    return is_document_name(name) == (view == VIEW_DOCUMENTS)


def documents_refusal(name: str) -> Optional[str]:
    """The sentence a non-document gets from the Documents view (any door:
    Files upload, folder upload, move, import, the Documents API), or None
    when ``name`` is a document."""
    if is_document_name(name):
        return None
    ext = ext_of(name)
    if ext in _PICTURE_EXTS:
        return ("Documents shows documents, spreadsheets and PDFs. Put pictures "
                "in Pictures, or upload this in Other.")
    # Video first: .mp4/.m4v are in both media sets.
    if ext in VIDEO_EXTENSIONS:
        return ("Documents shows documents, spreadsheets and PDFs. Put videos in "
                "Videos, or upload this in Other.")
    if ext in AUDIO_EXTENSIONS:
        return ("Documents shows documents, spreadsheets and PDFs. Put music in "
                "Music (or Audiobooks, Podcasts), or upload this in Other.")
    return OTHER_REFUSAL


def view_refusal(view: Optional[str], name: str) -> Optional[str]:
    """Why a new FILE named ``name`` may not be written through ``view``, or
    None. Folders are never refused here (they belong to both views)."""
    if view == VIEW_DOCUMENTS:
        return documents_refusal(name)
    if view == VIEW_OTHER and is_document_name(name):
        return DOCUMENT_IN_OTHER_REFUSAL
    return None
