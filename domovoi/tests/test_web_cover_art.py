"""Library cover art on the dashboard — the Music table's track cell, the
track drawer, the room now-playing cards and the player.

Every one of them draws ``/api/music/library/{id}/cover`` (read from the
file on request, 404 when there is none) through ``CoverArt``, asking for
the thumbnail that covers its tile at 2x (``?size=`` s 160px, m 320, l 512,
xl 768; ``coverBucket``) instead of the original picture, which can be
1.5 MB: a table cell, a drawer or a room card gets ``s``, the expanded
player's 200px tile ``l``, the OS media controls ``xl``.
(web/static/components.jsx): an <img> with its width and height set up
front so a row never jumps, ``loading="lazy"`` and ``decoding="async"``,
over a quiet placeholder that stays when the picture 404s. A cover that
failed is remembered for the page's life, so the same track rendered again
— a re-render, a page flipped back to, another row — never asks again, and
nothing is logged from the page. A miss is remembered per track (and
cover version), not per size: a track with no art has none at any size.

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
COVER6_S = COVER6 + "?size=s"
TRACK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
         "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
         "added_via": "manual", "favorited": False, "source": None, "source_id": None, "enriched_at": None}
STATS = {"total_tracks": 2, "total_duration_sec": 400, "by_added_via": {"manual": 2},
         "by_source": {"library": 2}, "enriched_count": 0}
LIB = "GET /api/music/library?sort=added_desc"


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
// `src` without a query matches the cover's path, whatever its ?v= / ?size=.
const imgs = (src) => h.findAll((e) => e.type === 'img' && (!src
  || String(e.props.src) === src || String(e.props.src).split('?')[0].endsWith(src)));
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


# Every <img> any render produced, transient ones included: the tree a
# scenario reads is only the LAST render, and a src that is in the DOM for a
# single commit is still a request the browser makes.
RECORD_IMGS = r"""
globalThis.__imgs = [];
const __createElement = React.createElement;
React.createElement = (type, p, ...c) => {
  if (type === 'img' && p) globalThis.__imgs.push({ src: p.src, opacity: (p.style || {}).opacity });
  return __createElement(type, p, ...c);
};
"""

# One CoverArt that stays mounted while its src changes (the bar, a room
# card), beside a second one that has track 6's cover on its own.
SWAP = r"""(() => function CoverSwap() {
  const [src, setSrc] = React.useState('/api/music/library/7/cover');
  return React.createElement('div', null,
    React.createElement('button', { title: 'to-6', onClick: () => setSrc('/api/music/library/6/cover') }),
    React.createElement('button', { title: 'to-8', onClick: () => setSrc('/api/music/library/8/cover') }),
    React.createElement(CoverArt, { src, size: 44 }),
    React.createElement(CoverArt, { src: '/api/music/library/6/cover', size: 72 }));
})()"""

# A 40px row's cover beside, after a click, the same track at 200px (same
# version, then a newer one) and another track at 200px.
MISS_SIZES = r"""(() => function MissSizes() {
  const [big, setBig] = React.useState(false);
  return React.createElement('div', null,
    React.createElement('button', { title: 'big', onClick: () => setBig(true) }),
    React.createElement(CoverArt, { src: libraryCoverUrl(6, 2, 40), size: 40 }),
    big && React.createElement(CoverArt, { src: libraryCoverUrl(6, 2, 200), size: 200 }),
    big && React.createElement(CoverArt, { src: libraryCoverUrl(6, 3, 200), size: 199 }),
    big && React.createElement(CoverArt, { src: libraryCoverUrl(7, 2, 200), size: 198 }));
})()"""


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
    # The expanded player's 200px tile asks for a large thumbnail, the
    # cover's version kept ahead of the size.
    "player_tile_sheet": {
        "files": PLAYER, "component": "CoverTile", "setup": QUIET,
        "props": {"item": {"coverUrl": COVER6 + "?v=3", "seekable": True}, "size": 200},
        "script": HELPERS + "h.render(); return { attrs: imgs().map(imgAttrs) };",
    },
    # The helpers themselves: the bucket for a tile, and what a URL becomes.
    "helpers": {
        "files": [MUSIC[0]], "component": "CoverArt", "setup": QUIET,
        "props": {"src": None},
        "script": r"""
            const W = h.global('window');          // components.jsx's exports
            const bucket = W.coverBucket, at = W.coverUrlAtSize, key = W.coverMissKey, lib = W.libraryCoverUrl;
            return {
              buckets: [32, 40, 52, 56, 72, 80, 81, 160, 161, 256, 257, 1000, 'xl', 'm']
                .map((c) => [c, bucket(c)]),
              urls: [at('/api/music/library/6/cover', 40),
                     at('/api/music/library/6/cover?v=3', 200),
                     at('/api/music/library/6/cover?v=3&size=s', 'xl'),
                     at('/api/audiobooks/9/cover', 40),
                     at('/api/podcasts/subscriptions/2/artwork', 40),
                     at(null, 40)],
              keys: [key('/api/music/library/6/cover?v=3&size=l'), key('/api/music/library/6/cover?size=s')],
              lib: [lib(6, 2, 40), lib(6, undefined, 200), lib(6, 2), lib(null, 2, 40)],
            };
        """,
    },
    "player_tile_stream": {
        "files": PLAYER, "component": "CoverTile", "setup": QUIET,
        "props": {"item": {"coverUrl": None, "seekable": False}, "size": 44},
        "script": HELPERS + "h.render(); return { imgs: imgs().length, covers: covers() };",
    },
    # A tile that stays mounted while its src changes — the bar, the Player
    # tab or a room card moving to the next track. The new src starts over
    # in the render that brings it: a known miss never reaches an <img>
    # (no second request, no second 404 line on the console), and a new
    # picture is never drawn at full opacity under the last one's "shown".
    "swap_to_known_miss": {
        "files": [MUSIC[0]], "component": SWAP, "setup": QUIET + RECORD_IMGS,
        "script": HELPERS + r"""
            h.render();
            await load('/api/music/library/7/cover');      // the 44px tile shows track 7
            await fail('/api/music/library/6/cover');      // the 72px tile: track 6 has none
            h.global('__imgs').length = 0;
            await h.click({ type: 'button', title: 'to-6' });
            return { imgs6: h.global('__imgs').filter((i) => String(i.src).endsWith('/6/cover')).length,
                     covers: covers(), logs: logs() };
        """,
    },
    "swap_to_new_cover": {
        "files": [MUSIC[0]], "component": SWAP, "setup": QUIET + RECORD_IMGS,
        "script": HELPERS + r"""
            h.render();
            await load('/api/music/library/7/cover');
            h.global('__imgs').length = 0;
            await h.click({ type: 'button', title: 'to-8' });
            const first = h.global('__imgs').find((i) => String(i.src).endsWith('/8/cover'));
            const mid = covers().find((c) => c.size === 44);
            await load('/api/music/library/8/cover');
            return { firstOpacity: first ? first.opacity : null, mid: mid.state,
                     after: covers().find((c) => c.size === 44).state };
        """,
    },
    # A cover that 404'd at one size is not asked for at another (the row's
    # s, then the player's l), but a new version of it is, and so is
    # another track's.
    "miss_is_per_track": {
        "files": [MUSIC[0]], "component": MISS_SIZES, "setup": QUIET + RECORD_IMGS,
        "script": HELPERS + r"""
            h.render();
            await fail('/api/music/library/6/cover?v=2&size=s');
            h.global('__imgs').length = 0;
            await h.click({ type: 'button', title: 'big' });
            const asked = h.global('__imgs').map((i) => i.src);
            return { asked, covers: covers(), logs: logs() };
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    # The scenarios go in a file (`@<path>`), as the other harness tests do:
    # on the command line they pass Windows' 32 KB limit (WinError 206).
    spec = tmp_path_factory.mktemp("cover-art") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
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
    assert r["desk"] == {"src": COVER6_S, "width": 40, "height": 40, **LAZY}   # the small thumbnail
    assert r["phone"] == {"src": COVER6_S, "width": 32, "height": 32, **LAZY}
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
    assert r["attrs"] == [{"src": COVER6_S, "width": 72, "height": 72, **LAZY}]
    assert r["gradients"] == 0


def test_room_cards_show_the_cover_of_the_library_file_they_play(driven):
    r = driven["rooms"]
    srcs = sorted(a["src"] for a in r["imgs"])
    assert srcs == ["/api/music/library/6/cover?size=s", "/api/music/library/9/cover?size=s"]   # office, garage
    assert all(a["height"] == 52 and a["loading"] == "lazy" for a in r["imgs"])
    states = sorted(c["state"] for c in r["covers"])
    assert states == ["loading", "loading", "none", "none"]                       # den (stream), attic (idle)
    plain = [c for c in r["covers"] if c["state"] == "none"]
    assert all(c["icons"] == [] for c in plain)                                   # the old plain tiles


def test_player_tile_draws_the_cover_and_falls_back_quietly(driven):
    r = driven["player_tile"]
    assert r["first"]["attrs"] == [{"src": COVER6_S, "width": 44, "height": 44, **LAZY}]   # the bar: small
    (c,) = r["after"]
    assert c["state"] == "none" and "linear-gradient" in c["bg"] and c["icons"] == ["music"]
    assert r["logs"] == []


def test_player_tile_for_a_stream_asks_for_nothing(driven):
    r = driven["player_tile_stream"]
    assert r["imgs"] == 0
    (c,) = r["covers"]
    assert c["icons"] == ["radio"] and c["bg"] == "var(--sunken)"


def test_a_tile_moving_to_a_known_missing_cover_never_asks_for_it(driven):
    r = driven["swap_to_known_miss"]
    assert r["imgs6"] == 0                                 # not even for one commit
    assert {c["size"]: c["state"] for c in r["covers"]} == {44: "none", 72: "none"}
    assert r["logs"] == []


def test_a_tile_moving_to_a_new_cover_starts_over_invisible(driven):
    r = driven["swap_to_new_cover"]
    assert r["firstOpacity"] == 0                          # not drawn under the last picture's "shown"
    assert (r["mid"], r["after"]) == ("loading", "shown")


def test_media_session_never_offers_a_known_missing_cover():
    src = (REPO_ROOT / "web/static/player.jsx").read_text(encoding="utf-8")
    assert "item.coverUrl && !coverMissed(item.coverUrl)" in src
    # ...asks for the largest thumbnail: a lock screen draws it big.
    assert "[{ src: coverUrlAtSize(item.coverUrl, 'xl'), sizes: '768x768' }]" in src
    # ...and declares no type: a library cover is whatever the file carries
    # (JPEG, PNG, WebP), so a hard-coded image/jpeg would mislabel most PNGs.
    block = src[src.index("const updateMediaSession"):]
    block = block[:block.index("new window.MediaMetadata")]
    assert "type:" not in block.split("const artwork", 1)[1]


def test_the_expanded_player_asks_for_a_large_cover(driven):
    assert driven["player_tile_sheet"]["attrs"] == [
        {"src": COVER6 + "?v=3&size=l", "width": 200, "height": 200, **LAZY}]


def test_the_size_is_the_smallest_bucket_covering_the_tile_at_2x(driven):
    r = driven["helpers"]
    assert dict((str(c), b) for c, b in r["buckets"]) == {
        "32": "s", "40": "s", "52": "s", "56": "s", "72": "s", "80": "s",
        "81": "m", "160": "m", "161": "l", "256": "l", "257": "xl", "1000": "xl",
        "xl": "xl", "m": "m"}
    assert r["urls"] == [
        "/api/music/library/6/cover?size=s",
        "/api/music/library/6/cover?v=3&size=l",
        "/api/music/library/6/cover?v=3&size=xl",         # one size, replaced
        "/api/audiobooks/9/cover",                        # audiobooks and podcasts as they were
        "/api/podcasts/subscriptions/2/artwork",
        None]
    assert r["keys"] == ["/api/music/library/6/cover?v=3", "/api/music/library/6/cover"]
    assert r["lib"] == ["/api/music/library/6/cover?v=2&size=s",
                        "/api/music/library/6/cover?size=l",
                        "/api/music/library/6/cover?v=2",          # no size: the original
                        None]


def test_a_miss_is_remembered_per_track_not_per_size(driven):
    r = driven["miss_is_per_track"]
    assert "/api/music/library/6/cover?v=2&size=l" not in r["asked"]   # same track, same version
    assert "/api/music/library/6/cover?v=3&size=l" in r["asked"]       # a new version is asked for
    assert "/api/music/library/7/cover?v=2&size=l" in r["asked"]       # another track is untouched
    states = {c["size"]: c["state"] for c in r["covers"]}
    assert states == {40: "none", 200: "none", 199: "loading", 198: "loading"}
    assert r["logs"] == []


def test_every_music_cover_the_dashboard_draws_is_sized():
    """No library cover URL is built by hand without a size (the room
    display, which doesn't load components.jsx, asks for xl itself)."""
    import re
    static = REPO_ROOT / "web/static"
    for name in ("music.jsx", "library_tools.jsx"):
        src = (static / name).read_text(encoding="utf-8")
        calls = re.findall(r"libraryCoverUrl\(([^()]*)\)", src)
        assert calls, name
        assert all(c.count(",") == 2 for c in calls), (name, calls)
    display = (static / "display.jsx").read_text(encoding="utf-8")
    assert "/api/music/library/${trackId}/cover?size=xl" in display
    player = (static / "player.jsx").read_text(encoding="utf-8")
    assert "<CoverArt src={coverUrlAtSize((item && item.coverUrl) || null, size)} size={size}" in player
