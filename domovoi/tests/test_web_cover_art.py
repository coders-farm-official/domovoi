"""Library cover art on the dashboard — the Music table's track cell, the
track drawer, the room now-playing cards and the player.

Every one of them draws ``/api/music/library/{id}/cover`` (read from the
file on request, 404 when there is none) through ``CoverArt``
(web/static/components.jsx): an <img> with its width and height set up
front so a row never jumps, ``loading="lazy"`` and ``decoding="async"``,
over a quiet placeholder that stays when the picture 404s. A cover that
failed is remembered for the page's life, so the same track rendered again
— a re-render, a page flipped back to, another row — never asks again, and
nothing is logged from the page.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React). An <img>'s load / error is fired by hand,
the way the browser would. DB-free; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
MUSIC = ["web/static/components.jsx", "web/static/music.jsx"]
PLAYER = ["web/static/components.jsx", "web/static/player.jsx"]

COVER6 = "/api/music/library/6/cover"
TRACK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
         "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
         "added_via": "manual", "favorited": False, "source": None, "source_id": None, "enriched_at": None}
STATS = {"total_tracks": 2, "total_duration_sec": 400, "by_added_via": {"manual": 2},
         "by_source": {"library": 2}, "enriched_count": 0}
LIB = "GET /api/music/library?sort=added_desc&limit=12&offset=0"


def page_api(now_playing=None, items=None) -> dict:
    items = items if items is not None else [TRACK]
    return {"GET /api/music/now-playing": now_playing or [], "GET /api/playlists": [],
            "GET /api/music/library/stats": STATS, "GET /api/acquisitions?limit=100": None,
            LIB: {"total": len(items), "items": items}}


# Every console method the page could reach records instead of printing.
QUIET = r"""
globalThis.__logs = [];
const __say = (lvl) => (...a) => globalThis.__logs.push([lvl, a.map(String).join(' ')]);
globalThis.console = { log: __say('log'), info: __say('info'), warn: __say('warn'),
                       error: __say('error'), debug: __say('debug') };
"""

HELPERS = r"""
const imgs = (src) => h.findAll((e) => e.type === 'img' && (!src || String(e.props.src).endsWith(src)));
const covers = () => h.findAll((e) => e.type === 'span' && e.props['data-cover'] != null)
  .map((e) => ({ state: e.props['data-cover'], size: e.props.style.width,
                 bg: e.props.style.background,
                 img: (h.findAll((i) => i.type === 'img' && h.inside(i, (a) => a === e))[0] || {}).props || null,
                 icons: h.findAll((i) => i.type === 'Icon' && h.inside(i, (a) => a === e)).map((i) => i.props.name) }));
const imgAttrs = (e) => ({ src: e.props.src, width: e.props.width, height: e.props.height,
                           loading: e.props.loading, decoding: e.props.decoding, alt: e.props.alt });
// What the browser does: one load / error event for each <img> that
// fetched the URL (its copies differ in size, which keys them here).
const each = async (src, handler) => {
  const done = new Set();
  for (;;) {
    const el = imgs(src).find((e) => !done.has(e.props.width));
    if (!el) return;
    done.add(el.props.width);
    await h.fire((e) => e === el, handler, {});
  }
};
const fail = (src) => each(src, 'onError');
const load = (src) => each(src, 'onLoad');
const wrapped = (cls) => (e) => h.inside(e, (a) => String(a.props.className || '') === cls);
const logs = () => h.global('__logs');
"""


def music(script: str, api: dict | None = None) -> dict:
    return {"files": MUSIC, "component": "MusicPage", "api": api or page_api(),
            "setup": QUIET, "script": HELPERS + script}


SCENARIOS = {
    # The table's stacked track cell leads with the cover; a phone draws
    # it in the play column instead (styles.css shows one copy per width).
    "cell": music(r"""
        h.render();
        const row = h.find({ type: 'tr', nth: 1 });
        const all = imgs('/api/music/library/6/cover');
        const desk = all.find(wrapped('lib-cover-desk'));
        const phone = all.find(wrapped('lib-cover-phone'));
        return { count: all.length, desk: imgAttrs(desk), phone: imgAttrs(phone),
                 inRow: all.every((e) => h.inside(e, (a) => a === row)),
                 deskInTrackCell: h.inside(desk, (e) => String(e.props.className || '') === 'lib-track'),
                 phoneUnderPlay: h.inside(phone, (e) => String(e.props.className || '') === 'lib-play')
                   && !!h.find((e) => e.type === 'button' && e.props.title === 'play in this browser'
                                      && h.inside(e, (a) => String(a.props.className || '') === 'lib-play')),
                 covers: covers(), headers: h.findAll({ type: 'th' }).map((e) => e.text).filter(Boolean) };
    """),
    # It loads: the picture shows, the placeholder glyph goes.
    "cell_loaded": music(r"""
        h.render();
        await load('/api/music/library/6/cover');
        return { covers: covers() };
    """),
    # It 404s: a quiet placeholder, no retry, nothing logged — and the same
    # track rendered again (a page flip back, the drawer) never asks again.
    "cell_404": music(r"""
        h.render();
        const before = imgs().length;
        await fail('/api/music/library/6/cover');
        const after = { covers: covers(), imgs: imgs().length };
        h.rerender();
        const rerendered = imgs('/api/music/library/6/cover').length;
        await h.click({ type: 'tr', nth: 1 });                 // the drawer, same track
        return { before, after, rerendered, drawerOpen: h.text().includes('track · #6'),
                 drawerImgs: imgs('/api/music/library/6/cover').length,
                 covers: covers(), logs: logs() };
    """),
    # Another track's cover is unaffected by the first one's 404.
    "cell_404_other_rows": music(r"""
        h.render();
        await fail('/api/music/library/6/cover');
        return { six: imgs('/api/music/library/6/cover').length, seven: imgs('/api/music/library/7/cover').length };
    """, api=page_api(items=[TRACK, {**TRACK, "id": 7, "title": "Ember Waltz"}])),
    # The drawer's square is the cover now, not a gradient placeholder.
    "drawer": music(r"""
        h.render();
        await h.click({ type: 'tr', nth: 1 });
        const drawer = h.find((e) => e.type === 'aside');
        const inDrawer = imgs('/api/music/library/6/cover').filter((e) => h.inside(e, (a) => a === drawer));
        const gradients = h.findAll((e) => e.type === 'div' && h.inside(e, (a) => a === drawer)
          && String((e.props.style || {}).background || '').includes('linear-gradient'));
        return { attrs: inDrawer.map(imgAttrs), gradients: gradients.length };
    """),
    # Room cards: a room playing a library file shows its cover (the
    # now-playing row carries track_id); a stream or an idle room doesn't ask.
    "rooms": music(r"""
        h.render();
        return { imgs: imgs().map(imgAttrs).filter((a) => a.width === 52), covers: covers().filter((c) => c.size === 52) };
    """, api=page_api(now_playing=[
        {"room_id": "office", "state": "play", "track_id": 6, "elapsed_sec": 3,
         "song": {"file": "Album/t.mp3", "title": "Warm Stones", "duration_sec": 200}},
        {"room_id": "den", "state": "play", "track_id": None, "elapsed_sec": 3,
         "song": {"file": "http://radio.example/stream", "title": "a stream"}},
        {"room_id": "garage", "state": "pause", "track_id": 9, "elapsed_sec": 3,
         "song": {"file": "Other/u.mp3", "title": "Paused One", "duration_sec": 100}},
        {"room_id": "attic", "state": "stop", "track_id": 6, "song": None},
    ])),
    # The player's tile (bar 44px, sheet 200px, Player tab 220px).
    "player_tile": {
        "files": PLAYER, "component": "CoverTile", "setup": QUIET,
        "props": {"item": {"coverUrl": COVER6, "seekable": True}, "size": 44},
        "script": HELPERS + r"""
            h.render();
            const first = { attrs: imgs().map(imgAttrs), covers: covers() };
            await fail('/api/music/library/6/cover');
            return { first, after: covers(), logs: logs() };
        """,
    },
    "player_tile_stream": {
        "files": PLAYER, "component": "CoverTile", "setup": QUIET,
        "props": {"item": {"coverUrl": None, "seekable": False}, "size": 44},
        "script": HELPERS + "h.render(); return { imgs: imgs().length, covers: covers() };",
    },
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


LAZY = {"loading": "lazy", "decoding": "async", "alt": ""}


def test_table_cell_leads_with_a_sized_lazy_cover(driven):
    r = driven["cell"]
    assert r["count"] == 2                                # one per width; CSS shows one
    assert r["desk"] == {"src": COVER6, "width": 40, "height": 40, **LAZY}
    assert r["phone"] == {"src": COVER6, "width": 32, "height": 32, **LAZY}
    assert r["inRow"] and r["deskInTrackCell"] and r["phoneUnderPlay"]
    assert r["headers"] == ["track", "duration"]          # no new column
    phone, desk = r["covers"]
    assert (desk["state"], desk["size"], desk["icons"]) == ("loading", 40, ["music"])   # placeholder under it
    assert (phone["state"], phone["size"], phone["icons"]) == ("loading", 32, [])       # the play glyph is on top
    assert desk["img"]["style"]["opacity"] == 0           # no broken-image flash before it lands


def test_each_width_shows_one_copy_and_the_phone_keeps_the_track_cell_whole():
    css = (REPO_ROOT / "web/static/styles.css").read_text(encoding="utf-8")
    phone = css[css.index(".lib-cover-desk { display: inline-flex"):]
    phone = phone[phone.index("@media (max-width: 760px)"):]
    phone = phone[:phone.index("\n}\n") + 3]
    assert ".lib-cover-phone { display: none; }" in css    # desktop: the track cell's copy
    assert ".lib-cover-desk { display: none; }" in phone   # phone: the play column's copy
    assert ".lib-cover-phone { display: inline-flex; }" in phone
    assert "position: absolute; inset: 0;" in phone        # the play button sits over the cover


def test_a_loaded_cover_replaces_the_placeholder(driven):
    for c in driven["cell_loaded"]["covers"]:
        assert c["state"] == "shown"
        assert c["img"]["style"]["opacity"] == 1
        assert c["icons"] == []


def test_a_404_leaves_a_quiet_placeholder_and_is_never_asked_again(driven):
    r = driven["cell_404"]
    assert r["before"] == 2                                # the phone copy and the desktop copy
    assert r["after"]["imgs"] == 0
    phone, desk = r["after"]["covers"]
    assert desk["state"] == "none" and desk["img"] is None and desk["icons"] == ["music"]
    assert phone["state"] == "none" and phone["img"] is None
    assert (desk["size"], phone["size"]) == (40, 32)       # same boxes: the row doesn't move
    assert r["rerendered"] == 0
    assert r["drawerOpen"] and r["drawerImgs"] == 0        # the drawer doesn't retry it either
    assert {c["state"] for c in r["covers"]} == {"none"}
    assert r["logs"] == []                                 # nothing on the console


def test_one_tracks_404_does_not_hide_another_tracks_cover(driven):
    assert driven["cell_404_other_rows"] == {"six": 0, "seven": 2}


def test_drawer_shows_the_cover_instead_of_the_gradient_square(driven):
    r = driven["drawer"]
    assert r["attrs"] == [{"src": COVER6, "width": 72, "height": 72, **LAZY}]
    assert r["gradients"] == 0


def test_room_cards_show_the_cover_of_the_library_file_they_play(driven):
    r = driven["rooms"]
    srcs = sorted(a["src"] for a in r["imgs"])
    assert srcs == ["/api/music/library/6/cover", "/api/music/library/9/cover"]   # office, garage
    assert all(a["height"] == 52 and a["loading"] == "lazy" for a in r["imgs"])
    states = sorted(c["state"] for c in r["covers"])
    assert states == ["loading", "loading", "none", "none"]                       # den (stream), attic (idle)
    plain = [c for c in r["covers"] if c["state"] == "none"]
    assert all(c["icons"] == [] for c in plain)                                   # the old plain tiles


def test_player_tile_draws_the_cover_and_falls_back_quietly(driven):
    r = driven["player_tile"]
    assert r["first"]["attrs"] == [{"src": COVER6, "width": 44, "height": 44, **LAZY}]
    (c,) = r["after"]
    assert c["state"] == "none" and "linear-gradient" in c["bg"] and c["icons"] == ["music"]
    assert r["logs"] == []


def test_player_tile_for_a_stream_asks_for_nothing(driven):
    r = driven["player_tile_stream"]
    assert r["imgs"] == 0
    (c,) = r["covers"]
    assert c["icons"] == ["radio"] and c["bg"] == "var(--sunken)"


def test_media_session_never_offers_a_known_missing_cover():
    src = (REPO_ROOT / "web/static/player.jsx").read_text(encoding="utf-8")
    assert "item.coverUrl && !coverMisses.has(item.coverUrl)" in src
