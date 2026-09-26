"""The Music page's refused and failed actions read like every other page.

The radio Stations page stopped toasting ``play failed: 401 Unauthorized:
{"detail":"X-Device-Token or admin session required"}`` behind the pair
prompt (``test_web_radio_play_auth.py``). The core Music page still did,
from every catch block: each one printed ``${e.message}`` — the status line
plus the raw JSON body — so a paired-in-a-moment phone that tapped "play
something" saw the refusal as a crash, and a dismissed prompt read as a
broken endpoint. Two things, both pinned here:

* **The wording.** Every mutation's catch goes through
  ``reportMutationFailure`` (``data.js``, the ``mutationErrorText`` rule):
  a dismissed prompt says "cancelled" and why, a real failure names the
  server's own reason, and a refusal the prompt still owns says nothing.
* **The double tap.** The room plays ("play something", play in room,
  play / shuffle a playlist) had no in-flight guard. Two taps sent two
  POSTs; refused, both waited on the same prompt, so dismissing it toasted
  twice — and pairing replayed both, starting two random tracks. One play
  per room is in flight at a time now; another room's play still goes.

Driven for real: ``web/static/data.js`` (apiFetch, the refusal routing and
replay, ``mutationErrorText``), ``components.jsx`` and ``music.jsx`` load
into ``jsx_interact_harness.js`` with a scripted ``fetch`` and ``Auth``.
No DB, no ``requires_db`` — this must never skip; it needs ``node`` and
fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACT_HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
MUSIC = "web/static/music.jsx"

# What a device-tier route answers a browser with neither the household
# token nor a Bearer, and what an admin-tier one answers (admin_auth).
DEVICE_401 = "X-Device-Token or admin session required"
ADMIN_401 = "admin session required"

SETUP = r"""
globalThis.__net = [];
globalThis.__prompts = { pair: 0, login: 0, ensurePair: 0, ensureLogin: 0 };
globalThis.setTimeout = () => 0;      // toast expiry, debounce: irrelevant, and it keeps node alive
globalThis.WebSocket = function () {
  return { close() {}, send() {}, addEventListener() {}, readyState: 3 };
};
const NOW_PLAYING = [
  { room_id: 'kitchen', state: 'stop', song: null },
  { room_id: 'den', state: 'stop', song: null },
  { room_id: 'office', state: 'play', elapsed_sec: 12,
    song: { title: 'Warm Stones', artist: 'Hearth Ensemble', duration_sec: 200 } },
];
const PLAYLIST = { id: 2, name: 'W1b Mix', track_count: 1, is_virtual: false,
                   description: '', cover_color: '', cover_emoji: '', created_at: null };
const __resp = (status, body) => ({
  ok: status >= 200 && status < 300, status,
  statusText: status === 200 ? 'OK' : status === 401 ? 'Unauthorized' : 'Error',
  text: async () => body, json: async () => JSON.parse(body),
});
globalThis.Auth = {
  token: null,
  paired: !!globalThis.__PAIRED,
  credentialVersion: 0,
  status: { setup_complete: true, authenticated: false },
  headers() {
    const h = {};
    if (this.paired) h['X-Device-Token'] = 'household-token';
    if (this.token) h.Authorization = 'Bearer ' + this.token;
    return h;
  },
  deviceToken() { return this.paired ? 'household-token' : null; },
  isLoggedIn() { return !!this.token; },
  isPaired() { return this.paired; },
  subscribe() { return () => {}; },
  requestLogin() { globalThis.__prompts.login += 1; },
  requestPairing() { globalThis.__prompts.pair += 1; },
  // The person answering the prompts: pairs / signs in, or dismisses.
  ensureLoggedIn() {
    globalThis.__prompts.ensureLogin += 1;
    if (!globalThis.__ANSWER) return Promise.resolve(false);
    this.token = 'admin-bearer';
    return Promise.resolve(true);
  },
  ensurePaired() {
    globalThis.__prompts.ensurePair += 1;
    if (!globalThis.__ANSWER) return Promise.resolve(false);
    this.paired = true;
    return Promise.resolve(true);
  },
};
// The two admin-tier actions on this page; everything else it mutates
// is device tier.
const ADMIN_PATHS = ['/api/music/library/reindex', '/api/music/library/enrich'];
globalThis.fetch = async (url, opts) => {
  const o = opts || {};
  const method = (o.method || 'GET').toUpperCase();
  const headers = o.headers || {};
  const path = String(url).split('?')[0];
  if (method === 'GET') {
    if (path.endsWith('/api/music/now-playing')) return __resp(200, JSON.stringify(NOW_PLAYING));
    if (path.endsWith('/api/music/library')) return __resp(200, '{"total":0,"items":[]}');
    if (path.endsWith('/api/music/library/stats')) {
      return __resp(200, '{"total_tracks":0,"total_duration_sec":0,"by_added_via":{},"by_source":{},"enriched_count":0}');
    }
    if (path.endsWith('/api/acquisitions')) return __resp(200, '{"acquisitions":[]}');
    if (path.endsWith('/api/playlists')) return __resp(200, JSON.stringify([PLAYLIST]));
    if (path.includes('/api/files/browse')) return __resp(200, '{"writable":true}');
    return __resp(200, '[]');
  }
  let body = null;
  try { body = JSON.parse(o.body); } catch (_) {}
  globalThis.__net.push({ method, path, room: body && body.room_id,
                          deviceToken: headers['X-Device-Token'] || null,
                          auth: headers.Authorization || null });
  if (globalThis.__SERVER_ERROR) return __resp(500, '{"detail":"mpd is on fire"}');
  if (ADMIN_PATHS.includes(path)) {
    if (!headers.Authorization) return __resp(401, JSON.stringify({ detail: 'admin session required' }));
  } else if (!headers['X-Device-Token'] && !headers.Authorization) {
    return __resp(401, JSON.stringify({ detail: 'X-Device-Token or admin session required' }));
  }
  return __resp(200, '{"ok":true}');
};
"""

# Render, let the mount-time fetches land, run `act`, and read the toasts.
SCRIPT = r"""
  h.render();
  await h.settle();
  h.rerender();
  await h.settle();
  h.rerender();
  const iconButton = (name, nth = 0) => h.findAll((el) => el.type === 'button'
    && (el.props.children || []).some((c) => c && c.props && c.props.name === name))[nth];
  const tap = (el, times = 1) => {
    if (!el) throw new Error('nothing to tap');
    const out = [];
    for (let i = 0; i < times; i++) out.push(el.props.onClick({ stopPropagation() {}, preventDefault() {} }));
    return out;
  };
  const pending = await (async () => { __ACT__ })();
  await Promise.all((pending || []).map((p) => Promise.resolve(p).catch(() => {})));
  await h.settle();
  h.rerender();
  await h.settle();
  // useToast renders each toast as <div title="dismiss"><StatusDot/><span>text</span></div>.
  const toastTexts = h.findAll((el) => el.props && el.props.title === 'dismiss')
    .map((el) => (el.props.children || [])
      .filter((c) => c && c.type === 'span')
      .map((c) => (c.props.children || []).join(''))
      .join(''));
  return { net: h.global('__net'), prompts: h.global('__prompts'), toastTexts };
"""

PLAY_SOMETHING = "return tap(h.find({ type: 'button', text: 'play something' }), __TAPS__);"
PLAY_IN_TWO_ROOMS = (
    "return [...tap(h.find({ type: 'button', text: 'play something' })),"
    " ...tap(h.find({ type: 'button', text: 'play something', nth: 1 }))];"
)
PAUSE_OFFICE = "return tap(iconButton('pause'));"
RESCAN = "return tap(h.find({ type: 'button', text: 'Rescan library' }));"
PLAYLIST_ROW_PLAY = (
    "await h.click({ type: 'button', text: 'Playlists' });"
    " return tap(h.find({ type: 'button', title: 'play' }), 2);"
)


def _scenario(act: str, *, taps: int = 1, answer: bool = False, paired: bool = False,
              server_error: bool = False) -> dict:
    setup = (f"globalThis.__ANSWER = {json.dumps(answer)};\n"
             f"globalThis.__PAIRED = {json.dumps(paired)};\n"
             f"globalThis.__SERVER_ERROR = {json.dumps(server_error)};\n" + SETUP)
    return {
        "files": ["web/static/data.js", "web/static/components.jsx", MUSIC],
        "component": "MusicPage",
        "setup": setup,
        "script": SCRIPT.replace("__ACT__", act.replace("__TAPS__", str(taps))),
    }


SCENARIOS = {
    # An unpaired phone double-taps "play something"; the prompt is dismissed.
    "double_tap_dismissed": _scenario(PLAY_SOMETHING, taps=2),
    # The same double tap, and the prompt answered with the household token.
    "double_tap_paired": _scenario(PLAY_SOMETHING, taps=2, answer=True),
    # A paired browser starts two different rooms in the same instant.
    "two_rooms": _scenario(PLAY_IN_TWO_ROOMS, paired=True),
    # A server that is simply broken.
    "server_error": _scenario(PLAY_SOMETHING, paired=True, server_error=True),
    # The transport row, refused and dismissed.
    "pause_dismissed": _scenario(PAUSE_OFFICE),
    # An admin-tier action: the admin sign-in is what is asked for.
    "rescan_dismissed": _scenario(RESCAN),
    # The Playlists tab's row play, double-tapped on a paired browser.
    "playlist_double_tap": _scenario(PLAYLIST_ROW_PLAY, paired=True),
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    # Seven full-page scenarios are past Windows' command-line limit: hand
    # the harness a file instead.
    scenarios = tmp_path_factory.mktemp("music_toasts") / "scenarios.json"
    scenarios.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(INTERACT_HARNESS), str(REPO_ROOT), f"@{scenarios}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


def _posts(o: dict, suffix: str) -> list[dict]:
    return [c for c in o["net"] if c["path"].endswith(suffix)]


def test_a_double_tap_sends_one_play_and_says_cancelled_once(outcomes) -> None:
    o = outcomes["double_tap_dismissed"]
    assert len(_posts(o, "/api/music/play")) == 1, o["net"]
    assert o["prompts"]["ensurePair"] == 1, o["prompts"]
    assert o["prompts"]["ensureLogin"] == 0 and o["prompts"]["login"] == 0
    assert o["toastTexts"] == [
        "shuffle requested in kitchen…",
        "play cancelled — this browser is not paired.",
    ], o["toastTexts"]


def test_no_toast_carries_the_raw_refusal(outcomes) -> None:
    for name in ("double_tap_dismissed", "pause_dismissed", "rescan_dismissed"):
        for text in outcomes[name]["toastTexts"]:
            assert "401" not in text and "{" not in text and "detail" not in text, (name, text)


def test_pairing_at_the_prompt_replays_one_play(outcomes) -> None:
    o = outcomes["double_tap_paired"]
    plays = _posts(o, "/api/music/play")
    # The refused attempt, then its one replay carrying the household token
    # — not a second random track from the second tap.
    assert [c["deviceToken"] for c in plays] == [None, "household-token"], plays
    assert o["toastTexts"] == ["shuffle requested in kitchen…"], o["toastTexts"]


def test_the_guard_is_per_room(outcomes) -> None:
    o = outcomes["two_rooms"]
    assert [c["room"] for c in _posts(o, "/api/music/play")] == ["kitchen", "den"], o["net"]


def test_a_real_failure_names_the_servers_reason(outcomes) -> None:
    o = outcomes["server_error"]
    assert o["toastTexts"] == [
        "shuffle requested in kitchen…",
        "play failed: mpd is on fire",
    ], o["toastTexts"]


def test_a_dismissed_transport_action_reads_cancelled(outcomes) -> None:
    o = outcomes["pause_dismissed"]
    assert [c["path"] for c in o["net"]] == ["/api/music/pause/office"], o["net"]
    assert o["toastTexts"] == ["pause cancelled — this browser is not paired."], o["toastTexts"]


def test_an_admin_action_asks_for_the_sign_in_and_says_so(outcomes) -> None:
    o = outcomes["rescan_dismissed"]
    assert o["prompts"]["ensureLogin"] == 1 and o["prompts"]["ensurePair"] == 0, o["prompts"]
    assert o["toastTexts"] == ["rescan cancelled — not signed in."], o["toastTexts"]


def test_a_playlist_double_tap_starts_it_once(outcomes) -> None:
    o = outcomes["playlist_double_tap"]
    plays = _posts(o, "/api/music/play-playlist")
    assert [c["room"] for c in plays] == ["kitchen"], o["net"]
    assert o["toastTexts"] == ["playing W1b Mix in kitchen…"], o["toastTexts"]


def test_no_music_mutation_toasts_a_raw_exception() -> None:
    """Every mutation's catch on the page goes through
    ``reportMutationFailure``. ``e.message`` is read once, to tell a
    playlist's duplicate-track 409 from a failure — never toasted."""
    src = (REPO_ROOT / MUSIC).read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", src, flags=re.S)
    assert re.findall(r"fire\(`[^`]*\$\{e\.message", code) == []
    assert re.findall(r"fire\(`[^`]*failed: \$\{apiErrorText\(e\)", code) == []
    assert code.count("e.message") == 1
    assert code.count("reportMutationFailure(fire, ") >= 20
