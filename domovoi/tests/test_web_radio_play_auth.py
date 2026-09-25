"""Playing the radio from a browser that is not signed in as an admin.

Kamron's phone, 2026-09-25: tapping "play here" on a favorite showed TWO
identical toasts, ``play failed: 401 Unauthorized: {"detail":"admin
session required"}``. Two causes, both pinned here:

* **The tier.** Every radio mutation sat on the plugin admin default, so a
  paired phone could not play a station. Play (and favorite, edit, forget,
  simulcast lookup) is ``@device_endpoint`` now; the refusal an unpaired
  browser gets names ``X-Device-Token``, which ``data.js`` answers with
  the "pair this browser" prompt and a replay — not the admin sign-in.
* **The second toast.** ``playStation`` had no in-flight guard. A tap that
  shows nothing for a round trip gets tapped again (a double tap on a
  phone is two clicks), every copy refused for want of a credential
  waited on the SAME prompt, and dismissing it once rejected all of them —
  one toast per tap. Reproduced in a browser against the pre-fix page:
  one double-click, one prompt, one cancel, two POSTs, two toasts.

The page is driven for real: ``web/static/data.js`` (the real apiFetch,
refusal routing, replay and ``mutationErrorText``), ``components.jsx`` and
the radio ``stations.jsx`` load into ``jsx_interact_harness.js`` with a
scripted ``fetch`` and ``Auth``. No DB, no ``requires_db`` — this must
never skip; it needs ``node`` and fails without it.
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
STATIONS = "plugins/radio/web/static/stations.jsx"

# What POST /api/plugins/radio/play answers a browser with no household
# token and no Bearer once setup is done (admin_auth.require_device).
DEVICE_401 = "X-Device-Token or admin session required"

SETUP = r"""
globalThis.__net = [];
globalThis.__prompts = { pair: 0, login: 0, ensurePair: 0, ensureLogin: 0 };
globalThis.__played = [];
globalThis.setTimeout = () => 0;      // toast expiry: irrelevant here, and it keeps node alive
globalThis.WebSocket = function () {
  return { close() {}, send() {}, addEventListener() {}, readyState: 3 };
};
const LOFI = {
  id: 1, name: 'Lofi 24/7', source: 'online', stream_url: 'https://example.com/lofi.mp3',
  favorited: true, sample_interval_sec: 180, tags: [], country_code: 'US',
  now_playing: null, icy_supported: null, last_sampled_at: null,
};
const __resp = (status, body) => ({
  ok: status >= 200 && status < 300, status,
  statusText: status === 200 ? 'OK' : status === 401 ? 'Unauthorized' : 'Error',
  text: async () => body, json: async () => JSON.parse(body),
});
globalThis.usePlayback = () => ({
  available: true,
  playItems: (items) => { globalThis.__played.push(items.map((i) => i.src)); },
});
globalThis.Auth = {
  token: null,
  paired: false,
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
  ensureLoggedIn() { globalThis.__prompts.ensureLogin += 1; return Promise.resolve(false); },
  // The person answering the pair prompt: pairs, or dismisses it.
  ensurePaired() {
    globalThis.__prompts.ensurePair += 1;
    if (!globalThis.__PAIR) return Promise.resolve(false);
    this.paired = true;
    return Promise.resolve(true);
  },
};
globalThis.fetch = async (url, opts) => {
  const o = opts || {};
  const method = (o.method || 'GET').toUpperCase();
  const headers = o.headers || {};
  const path = String(url).split('?')[0];
  if (method === 'GET') {
    if (path.endsWith('/radio/stations')) return __resp(200, JSON.stringify([LOFI]));
    if (path.endsWith('/radio/badge')) return __resp(200, '{"favorites":1}');
    if (path.includes('/api/files/browse')) return __resp(200, '{"writable":true}');
    return __resp(200, '[]');
  }
  globalThis.__net.push({ method, path, deviceToken: headers['X-Device-Token'] || null,
                          auth: headers.Authorization || null });
  if (globalThis.__SERVER_ERROR) return __resp(500, '{"detail":"database is on fire"}');
  // Device tier: the household token or a Bearer passes.
  if (!headers['X-Device-Token'] && !headers.Authorization) {
    return __resp(401, JSON.stringify({ detail: globalThis.__REFUSAL }));
  }
  return __resp(200, JSON.stringify(LOFI));
};
"""

# Open Lofi 24/7's details, then press "play here" — twice in one tick (a
# double tap: the second click lands while the first request is still in
# flight), or once.
SCRIPT = r"""
  h.render();
  await h.settle();
  h.rerender();
  await h.click({ type: 'button', title: 'details & detections' });
  const btn = h.find({ type: 'button', text: 'play here' });
  if (!btn) return { error: 'no play here button', text: h.text() };
  const taps = [];
  for (let i = 0; i < globalThis.__TAPS; i++) taps.push(btn.props.onClick());
  await Promise.all(taps.map((p) => Promise.resolve(p).catch(() => {})));
  await h.settle();
  h.rerender();
  await h.settle();
  // useToast renders each toast as <div title="dismiss"><StatusDot/><span>text</span></div>.
  const toastTexts = h.findAll((el) => el.props && el.props.title === 'dismiss')
    .map((el) => (el.props.children || [])
      .filter((c) => c && c.type === 'span')
      .map((c) => (c.props.children || []).join(''))
      .join(''));
  return {
    net: h.global('__net'),
    prompts: h.global('__prompts'),
    played: h.global('__played'),
    toastCount: toastTexts.length,
    toastTexts,
  };
"""


def _scenario(*, taps: int, pair: bool, refusal: str = DEVICE_401,
              server_error: bool = False) -> dict:
    setup = (f"globalThis.__TAPS = {taps};\n"
             f"globalThis.__PAIR = {json.dumps(pair)};\n"
             f"globalThis.__REFUSAL = {json.dumps(refusal)};\n"
             f"globalThis.__SERVER_ERROR = {json.dumps(server_error)};\n" + SETUP)
    return {
        "files": ["web/static/data.js", "web/static/components.jsx", STATIONS],
        "component": "window.DomovoiPlugins.radio.pages.StationsPage",
        "setup": setup,
        "script": SCRIPT.replace("globalThis.__TAPS", str(taps)),
    }


SCENARIOS = {
    # The report: an unpaired phone, a double tap, the prompt dismissed.
    "double_tap_dismissed": _scenario(taps=2, pair=False),
    # The same double tap, and the prompt answered with the household token.
    "double_tap_paired": _scenario(taps=2, pair=True),
    # A single tap against a server that is simply broken.
    "server_error": _scenario(taps=1, pair=False, server_error=True),
}


@pytest.fixture(scope="module")
def outcomes() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(INTERACT_HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
        assert "error" not in res, f"{name}: {res}"
    return out


def test_a_double_tap_sends_one_play_and_says_one_thing(outcomes) -> None:
    o = outcomes["double_tap_dismissed"]
    plays = [c for c in o["net"] if c["path"].endswith("/radio/play")]
    assert len(plays) == 1, o["net"]
    assert o["toastCount"] == 1, o
    assert o["played"] == []


def test_the_refusal_asks_to_pair_not_to_sign_in(outcomes) -> None:
    """A device-tier refusal opens the PAIR prompt (which itself offers the
    admin sign-in); the admin password alone is never what is asked for."""
    o = outcomes["double_tap_dismissed"]
    assert o["prompts"]["ensurePair"] == 1, o["prompts"]
    assert o["prompts"]["ensureLogin"] == 0 and o["prompts"]["login"] == 0


def test_a_dismissed_prompt_reads_cancelled_not_a_raw_401(outcomes) -> None:
    o = outcomes["double_tap_dismissed"]
    assert o["toastTexts"] == ["play cancelled — this browser is not paired."], o
    assert not any("401" in t or "detail" in t for t in o["toastTexts"])


def test_pairing_at_the_prompt_replays_the_play_and_the_station_streams(outcomes) -> None:
    o = outcomes["double_tap_paired"]
    plays = [c for c in o["net"] if c["path"].endswith("/radio/play")]
    # The refused attempt, then its one replay carrying the household token.
    assert [c["deviceToken"] for c in plays] == [None, "household-token"], plays
    assert o["played"] == [["/api/plugins/radio/stations/1/stream"]]
    assert o["toastTexts"] == ["playing Lofi 24/7"], o


def test_a_real_failure_still_says_so_with_the_servers_reason(outcomes) -> None:
    o = outcomes["server_error"]
    assert o["toastTexts"] == ["play failed: database is on fire"], o
    assert o["prompts"]["ensurePair"] == 0 and o["prompts"]["ensureLogin"] == 0


def test_no_station_mutation_toasts_a_raw_exception() -> None:
    """Every mutation's catch on the Stations page goes through
    ``reportFailure`` (mutationErrorText); only the two READS (search, the
    FCC status poll) may print ``e.message``, because a GET is never
    replayed and has no prompt to defer to."""
    src = (REPO_ROOT / STATIONS).read_text(encoding="utf-8")
    raw = re.findall(r"fire\(`([a-z ]+) failed: \$\{e\.message\}`\)", src)
    assert sorted(raw) == ["fcc status check", "search"], raw
    assert src.count("reportFailure(fire, ") >= 8
