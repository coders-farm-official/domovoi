"""Load as you scroll on the dashboard's files-like pages (owner decision
2026-10-11): Music's Library tab, Videos, Files, Audiobooks and Podcasts.

Driven for real: ``web/static/data.js`` (``usePagedList`` and
``PagedListFooter``, apiFetch underneath) and each page file, inside
``jsx_interact_harness.js`` with a scripted ``fetch`` that answers the
server's paging contract (web/backend/api/paging.py) over synthetic
lists. What is pinned:

* the first request asks for ONE page (``limit``), never the whole list;
* "showing N of M" counts what is loaded against the server's total;
* the next page is asked for with the previous answer's cursor — by the
  IntersectionObserver sentinel coming into view, or the "load more"
  button — and a page that leaves the sentinel in view asks again;
* a failed page leaves a retry row that asks for the SAME page again;
* a row the server sends twice is shown once;
* a search / filter / folder change starts over at page one, filtered by
  the server (no client-side filtering of a loaded page);
* "select all" on Files and Music means the loaded rows, and says so.

DB-free; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
BASE_FILES = ["web/static/data.js", "web/static/components.jsx"]

SETUP = r"""
globalThis.__net = [];
globalThis.__io = [];
globalThis.__failNext = {};
// Debounce timers run on the next tick (the search box), nothing lingers.
globalThis.setTimeout = (fn) => { Promise.resolve().then(() => { try { fn(); } catch (_) {} }); return 0; };
globalThis.WebSocket = function () { return { close() {}, send() {}, addEventListener() {}, readyState: 3 }; };
// A sentinel the test can bring into view.
globalThis.IntersectionObserver = class {
  constructor(cb, opts) { this.cb = cb; this.opts = opts; this.observed = []; globalThis.__io.push(this); }
  observe(el) { this.observed.push(el); }
  disconnect() { this.gone = true; }
};
globalThis.SpokenAudio = { clientId: () => 'dev-1', getPerson: () => null };
globalThis.ListeningAsSelector = () => null;
const __mk = (n, f) => Array.from({ length: n }, (_, i) => f(i));
globalThis.__lists = {
  '/api/music/library': { key: 'items', rows: __mk(120, (i) => ({
    id: i + 1, title: `Track ${i + 1}`, artist: 'Hearth', album: null, duration_sec: 180,
    file_path: `t${i + 1}.mp3`, added_at: null, added_via: 'manual', source: null,
    favorited: false })) },
  '/api/videos/list': { key: 'videos', rows: __mk(75, (i) => ({
    library_id: 'core:videos', library_label: 'Videos', rel: `clips/clip ${i}.mp4`,
    name: `clip ${i}.mp4`, size: 1000 + i, mtime: 1760000000 + i })),
    extra: { libraries: [{ library_id: 'core:videos', library_label: 'Videos', total: 75 }],
             truncated: false } },
  '/api/files/browse': { key: 'entries', rows: [
      ...__mk(3, (i) => ({ name: `folder ${i}`, rel: `folder ${i}`, is_dir: true, size: null,
                           mtime: 1760000000, kind: 'folder', locked_by: null })),
      ...__mk(87, (i) => ({ name: `file ${i}.txt`, rel: `file ${i}.txt`, is_dir: false,
                            size: 10, mtime: 1760000000, kind: 'doc-text', locked_by: null }))],
    extra: { library_id: 'core:music', path: '', editable: true, importable: true,
             doc_editing: false, breadcrumb: [], writable: true, blocked_reason: null } },
  '/api/audiobooks': { key: 'items', rows: __mk(64, (i) => ({
    id: i + 1, title: `Book ${i + 1}`, author: 'A', artwork: null, chapters: [],
    is_folder: false, duration_sec: 3600 })) },
  '/api/podcasts/subscriptions': { key: 'items', rows: __mk(55, (i) => ({
    id: i + 1, title: `Show ${i + 1}`, feed_url: `https://x.invalid/${i}`, artwork: null,
    episode_count: 3, downloaded_count: 1 })) },
};
const __resp = (status, body) => ({
  ok: status >= 200 && status < 300, status, statusText: status === 200 ? 'OK' : 'Error',
  text: async () => body, json: async () => JSON.parse(body),
});
globalThis.Auth = {
  token: null, paired: true, credentialVersion: 0,
  status: { setup_complete: true, authenticated: false },
  headers() { return { 'X-Device-Token': 'household-token' }; },
  deviceToken() { return 'household-token'; },
  isLoggedIn() { return false; }, isPaired() { return true; },
  subscribe() { return () => {}; },
  requestLogin() {}, requestPairing() {},
  ensureLoggedIn() { return Promise.resolve(false); },
  ensurePaired() { return Promise.resolve(true); },
};
globalThis.fetch = async (url, opts) => {
  const o = opts || {};
  const method = (o.method || 'GET').toUpperCase();
  // (no URL in the harness sandbox: split by hand)
  const raw = String(url).replace(/^https?:\/\/[^/]+/, '');
  const qi = raw.indexOf('?');
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const qs = {};
  new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '').forEach((v, k) => { qs[k] = v; });
  if (method !== 'GET') { globalThis.__net.push({ method, path, qs }); return __resp(200, '{}'); }
  const L = globalThis.__lists[path];
  if (L) {
    globalThis.__net.push({ method, path, qs });
    if (qs.cursor && globalThis.__failNext[path] > 0) {
      globalThis.__failNext[path] -= 1;
      return __resp(500, '{"detail":"disk on fire"}');
    }
    const needle = (qs.q || '').toLowerCase();
    let rows = needle ? L.rows.filter((r) => JSON.stringify(r).toLowerCase().includes(needle)) : L.rows;
    if (path === '/api/files/browse' && qs.path) rows = rows.slice(0, 2).map((r) => ({ ...r, rel: `${qs.path}/${r.name}` }));
    const limit = Number(qs.limit || 0);
    if (!limit) return __resp(200, JSON.stringify(L.key === 'items' && !L.extra ? rows : { ...(L.extra || {}), [L.key]: rows }));
    const start = qs.cursor ? Number(qs.cursor) : 0;
    // The server repeats the last row of the previous page once (a row
    // that moved): the client must not show it twice.
    const from = start && globalThis.__repeat ? start - 1 : start;
    const page = rows.slice(from, start + limit);
    const more = start + limit < rows.length;
    return __resp(200, JSON.stringify({ ...(L.extra || {}), [L.key]: page, total: rows.length,
      limit, has_more: more, next_cursor: more ? String(start + limit) : null }));
  }
  if (path === '/api/files/libraries') {
    return __resp(200, JSON.stringify({ libraries: [{ id: 'core:music', label: 'Music', kind: 'core',
      icon: 'music', kind_icon: 'folder', editable: true, importable: true, doc_editing: false }] }));
  }
  if (path.endsWith('/api/music/library/stats')) {
    return __resp(200, '{"total_tracks":120,"total_duration_sec":0,"by_added_via":{},"by_source":{},"enriched_count":0}');
  }
  if (path.endsWith('/api/acquisitions')) return __resp(200, '{"acquisitions":[]}');
  if (path.endsWith('/api/videos/recent')) return __resp(200, '{"recent":[]}');
  return __resp(200, '[]');
};
"""

PRELUDE = r"""
  h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const reqs = (p) => h.global('__net').filter((r) => r.path === p).map((r) => r.qs);
  const footer = () => h.text().filter((t) => t.startsWith('showing ') || t.startsWith("couldn't"));
  const step = async () => { await h.settle(); h.rerender(); await h.settle(); h.rerender(); };
  const io = () => h.global('__io').filter((x) => !x.gone && x.observed.length);
"""


def _scenario(page_files, component, script, *, setup_extra="") -> dict:
    return {
        "files": BASE_FILES + page_files,
        "component": component,
        "setup": setup_extra + SETUP,
        "refs": True,
        "script": PRELUDE + script,
    }


SCENARIOS = {
    "music_scroll": _scenario(["web/static/music.jsx"], "MusicPage", r"""
      const first = footer();
      await h.click({ type: 'button', text: 'load more' }); await step();
      const second = footer();
      // The sentinel comes into view: the rest loads, page after page.
      io().forEach((x) => x.cb([{ isIntersecting: true }])); await step(); await step();
      const third = footer();
      const rows = h.findAll({ type: 'tr' }).length - 1;
      const sel = h.find((el) => el.type === 'input' && String(el.props.title || '').includes('loaded track'));
      await h.type({ type: 'input', placeholder: 'search title, artist, album, path…' }, 'Track 11');
      await step(); await step();
      return { first, second, third, rows, selTitle: sel && sel.props.title,
               after: footer(), reqs: reqs('/api/music/library'),
               moreButtons: h.findAll({ type: 'button', text: 'load more' }).length };
    """),
    "music_retry": _scenario(["web/static/music.jsx"], "MusicPage", r"""
      h.global('__failNext')['/api/music/library'] = 1;
      await h.click({ type: 'button', text: 'load more' }); await step();
      const failed = footer();
      const retry = h.find({ type: 'button', text: 'retry' });
      await h.click({ type: 'button', text: 'retry' }); await step();
      return { failed, hadRetry: !!retry, after: footer(), reqs: reqs('/api/music/library') };
    """),
    "music_no_duplicates": _scenario(["web/static/music.jsx"], "MusicPage", r"""
      await h.click({ type: 'button', text: 'load more' }); await step();
      const titles = h.findAll({ type: 'div' }).map((d) => d.text).filter((t) => /^Track \d+$/.test(t));
      return { titles, footer: footer() };
    """, setup_extra="globalThis.__repeat = true;\n"),
    "videos": _scenario(["web/static/videos.jsx"], "VideosPage", r"""
      const first = footer();
      await h.type({ type: 'input', placeholder: 'filter…' }, 'clip 7');
      await step(); await step();
      const names = h.findAll({ type: 'div' }).map((d) => d.text).filter((t) => /^clip \d+\.mp4$/.test(t));
      return { first, after: footer(), names, reqs: reqs('/api/videos/list') };
    """),
    "files": _scenario(["web/static/files.jsx"], "FilesPage", r"""
      await step();
      const first = footer();
      const sel = h.find((el) => el.type === 'input' && el.props.type === 'checkbox'
                                 && String(el.props.title || '').startsWith('select the'));
      const selTitle = sel && sel.props.title;
      await h.type({ type: 'input', placeholder: 'filter this folder…' }, 'file 8');
      await step(); await step();
      const filtered = footer();
      return { first, selTitle, filtered, reqs: reqs('/api/files/browse') };
    """),
    "audiobooks": _scenario(["web/static/audiobooks.jsx"], "AudiobooksPage", r"""
      const first = footer();
      await h.click({ type: 'button', text: 'load more' }); await step();
      return { first, after: footer(), reqs: reqs('/api/audiobooks') };
    """),
    "podcasts": _scenario(["web/static/podcasts.jsx"], "PodcastsPage", r"""
      const first = footer();
      await h.type({ type: 'input', placeholder: 'search shows…' }, 'Show 5');
      await step(); await step();
      return { first, after: footer(), reqs: reqs('/api/podcasts/subscriptions') };
    """),
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    f = tmp_path_factory.mktemp("paged") / "scenarios.json"
    f.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{f}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


def _has_no_offset_and_a_limit(q: dict) -> None:
    assert "limit" in q and "offset" not in q, q


def test_music_loads_one_page_and_scrolls_for_more(outcomes):
    o = outcomes["music_scroll"]
    reqs = o["reqs"]
    _has_no_offset_and_a_limit(reqs[0])
    assert reqs[0]["limit"] == "50" and "cursor" not in reqs[0]
    assert o["first"] == ["showing 50 of 120 tracks"]
    assert o["second"] == ["showing 100 of 120 tracks"]
    assert [r.get("cursor") for r in reqs[:3]] == [None, "50", "100"]
    assert o["third"] == ["showing 120 of 120 tracks"]
    assert o["rows"] == 120
    assert "120 loaded" in o["selTitle"]


def test_music_search_starts_over_on_the_server(outcomes):
    o = outcomes["music_scroll"]
    last = o["reqs"][-1]
    assert last["q"] == "Track 11" and "cursor" not in last and last["limit"] == "50"
    # Track 11, 110..119 — the server's filter over all 120, not the loaded rows.
    assert o["after"] == ["showing 11 of 11 tracks"]
    assert o["moreButtons"] == 0


def test_a_failed_page_offers_a_retry_of_the_same_page(outcomes):
    o = outcomes["music_retry"]
    assert o["hadRetry"]
    assert o["failed"] == ["showing 50 of 120 tracks", "couldn't load the next page"]
    assert [r.get("cursor") for r in o["reqs"]] == [None, "50", "50"]
    assert o["after"] == ["showing 100 of 120 tracks"]


def test_a_row_sent_twice_is_shown_once(outcomes):
    o = outcomes["music_no_duplicates"]
    assert len(o["titles"]) == len(set(o["titles"])) == 100
    assert o["footer"] == ["showing 100 of 120 tracks"]


def test_videos_page_and_filter_on_the_server(outcomes):
    o = outcomes["videos"]
    first, last = o["reqs"][0], o["reqs"][-1]
    assert first["limit"] == "50" and first["sort"] == "library" and "q" not in first
    assert o["first"] == ["showing 50 of 75 videos"]
    assert last["q"] == "clip 7" and "cursor" not in last
    assert sorted(o["names"]) == sorted(["clip 7.mp4", *[f"clip {i}.mp4" for i in range(70, 75)]])
    assert o["after"] == ["showing 6 of 6 videos"]


def test_files_page_and_honest_select_all(outcomes):
    o = outcomes["files"]
    first = o["reqs"][0]
    assert first["limit"] == "50" and first["path"] == "" and first["device_id"]
    assert o["first"] == ["showing 50 of 90 items"]
    assert o["selTitle"] == "select the 50 loaded items (more load as you scroll)"
    last = o["reqs"][-1]
    assert last["q"] == "file 8" and "cursor" not in last
    assert o["filtered"] == ["showing 8 of 8 items"]   # file 8, file 80..86


def test_audiobooks_scroll(outcomes):
    o = outcomes["audiobooks"]
    assert o["reqs"][0]["limit"] == "50" and o["first"] == ["showing 50 of 64 books"]
    assert o["after"] == ["showing 64 of 64 books"]


def test_podcasts_search_on_the_server(outcomes):
    o = outcomes["podcasts"]
    assert o["reqs"][0]["limit"] == "50" and o["first"] == ["showing 50 of 55 shows"]
    assert o["reqs"][-1]["q"] == "Show 5" and o["after"] == ["showing 7 of 7 shows"]


def test_every_page_uses_the_shared_hook():
    """No files-like page fetches its list outside usePagedList (which is
    what keeps "only a page at a time" true)."""
    for page, path in (("music.jsx", "/api/music/library?"), ("videos.jsx", "/api/videos/list"),
                       ("files.jsx", "/api/files/browse"), ("audiobooks.jsx", "/api/audiobooks"),
                       ("podcasts.jsx", "/api/podcasts/subscriptions")):
        src = (REPO_ROOT / "web/static" / page).read_text(encoding="utf-8")
        assert "usePagedList(" in src, page
        assert f"useApiList('{path}" not in src and f"useApiObject('{path}" not in src, page
