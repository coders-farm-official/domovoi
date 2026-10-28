"""The formatted-document file: one self-contained HTML page.

:func:`document_file` wraps the editor's content (already sanitised) in
a page with a small embedded style block, so the file reads like a
document when it is opened in any browser, mailed, or copied to a stick:
a centred page-width column, the editor's headings, lists, checklists,
tables, quotes, code and pictures (left / centre / right, with captions).
Nothing is linked: no stylesheet, script or font is fetched. A font the
other computer doesn't have falls back to the next one in its list.

:func:`document_body` is the reverse, for loading: the content between
the markers this module writes, or, for an ``.html`` file from anywhere
else, its ``<body>``. Either way the caller sanitises what comes back.
"""

from __future__ import annotations

import html
import re

FORMAT_NAME = "domovoi-document"
FORMAT_VERSION = 1

BODY_START = "<!-- domovoi:document-body -->"
BODY_END = "<!-- /domovoi:document-body -->"

# Kept small and plain: it travels inside every document.
STYLE = """
:root { color-scheme: light; }
html { background: #f3f3f1; }
body { margin: 0; padding: 32px 16px; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #1d1d1f; }
.doc { box-sizing: border-box; max-width: 816px; margin: 0 auto; padding: 72px 80px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.12); min-height: 1056px; overflow-wrap: break-word; }
.doc > :first-child { margin-top: 0; }
h1 { font-size: 2em; line-height: 1.25; margin: .8em 0 .4em; }
h2 { font-size: 1.5em; line-height: 1.3; margin: .9em 0 .4em; }
h3 { font-size: 1.25em; margin: 1em 0 .4em; }
h4, h5, h6 { font-size: 1em; margin: 1em 0 .4em; }
p { margin: 0 0 .7em; }
a { color: #1a5fb4; }
blockquote { margin: 1em 0; padding: .2em 0 .2em 1em; border-left: 3px solid #c8c8c8; color: #555; }
pre { background: #f5f5f4; border-radius: 6px; padding: 12px 14px; overflow-x: auto; font: 14px/1.5 ui-monospace, "JetBrains Mono", Consolas, monospace; }
code { font-family: ui-monospace, "JetBrains Mono", Consolas, monospace; font-size: .92em; background: #f1f1ef; border-radius: 4px; padding: .1em .3em; }
pre code { background: none; padding: 0; font-size: inherit; }
hr { border: 0; border-top: 1px solid #d0d0d0; margin: 1.5em 0; }
mark { background: #fff3a3; color: inherit; padding: 0 .1em; }
ul, ol { padding-left: 1.6em; margin: 0 0 .7em; }
li > p { margin: 0 0 .2em; }
ul[data-type="taskList"] { list-style: none; padding-left: .2em; }
ul[data-type="taskList"] li { display: flex; gap: .5em; align-items: flex-start; }
ul[data-type="taskList"] li > label { flex: 0 0 auto; margin-top: .2em; }
ul[data-type="taskList"] li > div { flex: 1 1 auto; }
ul[data-type="taskList"] li[data-checked="true"] > div { color: #777; text-decoration: line-through; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; table-layout: fixed; }
td, th { border: 1px solid #c8c8c8; padding: 6px 8px; vertical-align: top; }
th { background: #f3f3f1; font-weight: 600; text-align: left; }
td > p, th > p { margin: 0; }
figure { margin: 1em auto; max-width: 100%; }
figure img { display: block; width: 100%; height: auto; }
figure[data-align="left"] { float: left; margin: .3em 1.2em .8em 0; }
figure[data-align="right"] { float: right; margin: .3em 0 .8em 1.2em; }
figure[data-align="center"] { margin-left: auto; margin-right: auto; }
figcaption { font-size: .88em; color: #666; text-align: center; margin-top: .4em; }
img { max-width: 100%; height: auto; }
.doc::after { content: ""; display: block; clear: both; }
@media (max-width: 700px) { body { padding: 0; background: #fff; } .doc { padding: 24px 16px; box-shadow: none; min-height: 0; }
  figure[data-align="left"], figure[data-align="right"] { float: none; margin: 1em auto; } }
@media print { html, body { background: #fff; padding: 0; } .doc { box-shadow: none; padding: 0; max-width: none; min-height: 0; } }
@page { margin: 2cm; }
""".strip()


def document_file(body_html: str, title: str = "") -> str:
    """The whole file for ``body_html`` (sanitised content, no wrapper)."""
    safe_title = html.escape(title or "Document", quote=False)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="generator" content="{FORMAT_NAME} {FORMAT_VERSION}">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>\n{STYLE}\n</style>\n"
        '</head>\n<body>\n<main class="doc">\n'
        f"{BODY_START}\n{body_html}\n{BODY_END}\n"
        "</main>\n</body>\n</html>\n"
    )


_BODY_OPEN = re.compile(r"<body(?=[\s/>])", re.I)
_BODY_CLOSE = re.compile(r"</body", re.I)


def looks_like_document(text: str) -> bool:
    """True when ``text`` is a file :func:`document_file` wrote."""
    return BODY_START in text and BODY_END in text


def document_body(text: str) -> str:
    """The content part of an ``.html`` file (NOT sanitised: the caller
    does that). Our own files: what is between the markers. Anything else:
    the ``<body>``, or the whole text when there is no body tag."""
    text = text or ""
    start = text.find(BODY_START)
    if start != -1:
        end = text.find(BODY_END, start)
        if end != -1:
            return text[start + len(BODY_START): end].strip()
    # Linear on purpose (no lazy regex over the whole file, which repeated
    # "<body" made quadratic): the first <body…> tag, then the LAST </body.
    m = _BODY_OPEN.search(text)
    if m:
        gt = text.find(">", m.end())
        if gt == -1:
            return ""
        # the LAST </body, found in the original text (not text.lower(),
        # whose offsets drift where lowercasing changes a character's length)
        last = None
        for last in _BODY_CLOSE.finditer(text, gt + 1):
            pass
        end = last.start() if last is not None else len(text)
        return text[gt + 1:end].strip()
    return text.strip()


def title_from_name(stem: str) -> str:
    return stem.strip() or "Document"
