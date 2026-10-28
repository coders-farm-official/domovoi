"""``content.has_content`` and the worker's ``preview`` op: what makes a
version "empty" (wf/editors-followups, the restore that cleared a document).

A brand-new document's first version is one empty paragraph; restoring it
clears the document, so the history panel says so and the restore route
asks first. A picture, a table, a list or a rule is content even without
text."""

from __future__ import annotations

import pytest
from pycrdt import Doc, Text, XmlElement, XmlFragment, XmlText

from web.backend.collab import content, worker


def _xml(build) -> Doc:
    d = Doc()
    build(d.get("default", type=XmlFragment))
    return d


def _para(frag, text=""):
    p = frag.children.append(XmlElement("paragraph"))
    t = p.children.append(XmlText())
    if text:
        t.insert(0, text)
    return p


def test_nothing_at_all_is_empty():
    assert content.has_content(Doc()) is False


def test_one_empty_paragraph_is_empty():
    assert content.has_content(_xml(lambda f: _para(f))) is False


def test_blank_lines_and_an_empty_heading_are_empty():
    def build(f):
        _para(f, "   ")
        h = f.children.append(XmlElement("heading"))
        h.attributes["level"] = 2
        p = _para(f)
        p.children.append(XmlElement("hardBreak"))
    assert content.has_content(_xml(build)) is False


def test_text_is_content():
    assert content.has_content(_xml(lambda f: _para(f, "milk"))) is True


@pytest.mark.parametrize("tag", ["figure", "table", "horizontalRule", "bulletList", "taskList"])
def test_blocks_are_content_without_text(tag):
    def build(f):
        _para(f)
        el = f.children.append(XmlElement(tag))
        if tag == "figure":
            el.attributes["src"] = "data:image/png;base64,iVBORw0KGgo="
    assert content.has_content(_xml(build)) is True


def test_the_text_root_counts_too():
    d = Doc()
    d.get("text", type=Text).insert(0, "\n\n")
    assert content.has_content(d) is False
    d.get("text", type=Text).insert(0, "x")
    assert content.has_content(d) is True


def test_the_worker_preview_reports_blank():
    blank = _xml(lambda f: _para(f)).get_update()
    full = _xml(lambda f: _para(f, "Dear Ana")).get_update()
    assert worker.handle(worker.Holder(), "preview", {}, [blank])[0] == {"text": "", "blank": True}
    out = worker.handle(worker.Holder(), "preview", {}, [full])[0]
    assert out["blank"] is False and out["text"] == "Dear Ana"
