"""A per-chapter folder book in the browser player (2026-10-20).

Before, the Audiobooks page made one queue item for every book with
``/api/audiobooks/{id}/audio`` and no ``?file=``, and the server refuses
that for a folder book (400): folder books never played in the browser.
Now ``itemsFromBook`` makes a folder book one item per file, in order,
each knowing where it starts in the book, and ``playSpoken`` takes that
list: a place in the BOOK starts in the file that holds it, that far in,
and a save is the place in the book again (one position per book, which
the phone and the rooms share). A single-file book is the one item it was.

Driven through domovoi/tests/jsx_interact_harness.js with the cast tests'
sandbox (stubbed audio elements, logged API calls). No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from domovoi.tests.test_web_player_cast import HARNESS, REPO_ROOT, _scenario

BOOK = r"""
const book = { id: 9, title: 'Dune', author: 'Frank Herbert', is_folder: true,
  cover_url: '/api/audiobooks/9/cover', artwork: '/api/audiobooks/9/cover',
  files: [
    { file: '01 Arrakis.mp3', title: 'Arrakis', start_sec: 0, duration_sec: 600, size_bytes: 10 },
    { file: "02 Muad'Dib & Sons.mp3", title: "Muad'Dib", start_sec: 600, duration_sec: 600, size_bytes: 10 },
  ] };
"""

SCENARIOS = {
    "resume_in_the_second_file": _scenario(
        BOOK +
        "h.render();"
        "const items = P().itemsFromBook(book);"
        "P().playSpoken(items, { resumeSec: 630 }); await step();"
        "const at = { src: el0().src, t: el0().currentTime, index: P().index,"
        "             queue: P().queue.map((i) => i.title) };"
        "clear(); P().pause(); await step();"
        "return { at, acts: acts(),"
        "  items: items.map((i) => ({ src: i.src, title: i.title, album: i.album,"
        "                             off: i.meta.offsetSec, cover: i.coverUrl })) };"
    ),
    "from_the_top": _scenario(
        BOOK +
        "h.render();"
        "P().playSpoken(P().itemsFromBook(book), { resumeSec: 0 }); await step();"
        "return { src: el0().src, t: el0().currentTime, index: P().index };"
    ),
    "single_file_book": _scenario(
        "h.render();"
        "const items = P().itemsFromBook({ id: 3, title: 'Emma', is_folder: false, cover_url: null,"
        "  files: [{ file: 'Emma.m4b', start_sec: 0 }], chapters: [{ title: 'One', start_sec: 0 }] });"
        "P().playSpoken(items, { resumeSec: 42 }); await step();"
        "clear(); P().pause(); await step();"
        "return { n: items.length, src: el0().src, t: el0().currentTime, cover: items[0].coverUrl,"
        "         chapters: items[0].chapters, acts: acts() };"
    ),
    # The Audiobooks page draws each book's cover through CoverArt (404 →
    # the book glyph stays) and its size beside its length.
    "page_covers": _scenario(
        "h.render(); await step();"
        "const spans = h.findAll((e) => e.type === 'span' && e.props['data-cover'] != null);"
        "const imgs = h.findAll((e) => e.type === 'img').map((e) => e.props.src);"
        "const meta = h.findAll((e) => e.type === 'div' && String(e.props.className || '') === 'mono')"
        "  .map((e) => e.text);"
        "return { states: spans.map((e) => e.props['data-cover']), imgs, meta };",
        files=["web/static/components.jsx", "web/static/player.jsx", "web/static/audiobooks.jsx"],
        component="AudiobooksPage",
        setup="globalThis.ListeningAsSelector = () => null;",
        api={"GET /api/audiobooks?sort=title": [
            {"id": 9, "title": "Dune", "author": "Frank Herbert", "is_folder": True,
             "cover_url": "/api/audiobooks/9/cover", "artwork": "/api/audiobooks/9/cover",
             "duration_sec": 1200, "size_bytes": 3 * 1024 * 1024, "chapters": [{}, {}], "files": []},
            {"id": 3, "title": "Emma", "author": None, "is_folder": False, "cover_url": None,
             "artwork": None, "duration_sec": 60, "size_bytes": None, "chapters": None, "files": []},
        ]},
    ),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("ab_folder") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_a_folder_book_is_one_item_per_file(driven):
    items = driven["resume_in_the_second_file"]["items"]
    assert [i["src"] for i in items] == [
        "/api/audiobooks/9/audio?file=01%20Arrakis.mp3",
        "/api/audiobooks/9/audio?file=02%20Muad'Dib%20%26%20Sons.mp3",
    ]
    assert [(i["title"], i["album"], i["off"]) for i in items] == [
        ("Arrakis", "Dune", 0), ("Muad'Dib", "Dune", 600)]
    assert all(i["cover"] == "/api/audiobooks/9/cover" for i in items)


def test_a_place_in_the_book_starts_in_the_file_that_holds_it(driven):
    out = driven["resume_in_the_second_file"]
    assert out["at"]["index"] == 1
    assert out["at"]["src"].endswith("file=02%20Muad'Dib%20%26%20Sons.mp3")
    assert out["at"]["t"] == 30
    assert out["at"]["queue"] == ["Arrakis", "Muad'Dib"]


def test_a_save_is_the_place_in_the_book(driven):
    posts = [a for a in driven["resume_in_the_second_file"]["acts"]
             if a.startswith("POST /api/audiobooks/9/position")]
    assert posts and '"position_sec":630' in posts[-1]


def test_from_the_top_is_the_first_file(driven):
    out = driven["from_the_top"]
    assert out["index"] == 0 and out["t"] == 0 and out["src"].endswith("file=01%20Arrakis.mp3")


def test_a_single_file_book_is_the_one_item_it_was(driven):
    out = driven["single_file_book"]
    assert out["n"] == 1 and out["src"] == "/api/audiobooks/3/audio"
    assert out["t"] == 42 and out["cover"] is None
    assert out["chapters"] == [{"title": "One", "start_sec": 0}]
    posts = [a for a in out["acts"] if a.startswith("POST /api/audiobooks/3/position")]
    assert posts and '"position_sec":42' in posts[-1]



def test_the_page_draws_the_cover_from_the_books_files_and_its_size(driven):
    out = driven["page_covers"]
    assert out["imgs"] == ["/api/audiobooks/9/cover"]       # Emma has none: no request
    assert out["states"] == ["loading", "none"]
    assert any("3.0 MB" in m and "2 chapters" in m for m in out["meta"]), out["meta"]
