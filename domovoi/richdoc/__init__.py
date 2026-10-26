"""Formatted documents: the Documents editor's ``.html`` files.

A formatted document is ONE self-contained HTML file: the editor's
content inside a small wrapper with an embedded style block, pictures
embedded as ``data:image/*`` URIs, so it looks right opened in any
browser and needs nothing from this server. People never see the word
"HTML": the Files page calls it a "Document".

These files can come from anywhere (a download, a USB stick, another
program), so nothing in them is trusted:

* :mod:`.sanitize`: the allowlist every load and every save goes
  through, on the server, whatever the browser already did. Only the tags
  and attributes the editor produces survive; scripts, event handlers,
  ``javascript:`` URLs, outside pictures and every other style property
  are dropped.
* :mod:`.fileformat`: the wrapper (:func:`document_file`) and its
  reverse (:func:`document_body`).
* :mod:`.text`: the document as plain markdown-style text, for the
  readers (Chat documents, read-aloud, printing).
* :mod:`.docx`: the document as Word (.docx), with python-docx.
"""

from __future__ import annotations

from domovoi.richdoc.fileformat import document_body, document_file, looks_like_document
from domovoi.richdoc.sanitize import sanitize_html
from domovoi.richdoc.text import html_to_markdown

__all__ = [
    "document_body",
    "document_file",
    "html_to_markdown",
    "looks_like_document",
    "sanitize_html",
]

#: The extensions that are formatted documents.
HTML_EXTS = frozenset({".html", ".htm"})
