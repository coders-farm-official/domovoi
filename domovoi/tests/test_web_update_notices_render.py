"""Update notices (1.5.0) in a REAL browser: the dashboard's own page,
rendered by headless Chrome, measured for wrapping and overflow.

The other update-notice tests (test_web_update_notices.py,
test_web_home_page.py) drive the JSX outside a browser, so they can say
what is rendered but not how it lays out. These load the real
``web/static/index.html`` (the vendored React and Babel, every real .jsx and
the real CSS) from a small uvicorn app that mounts ``web/static`` with the
web app's own ``RevalidatingStaticFiles``; only the ``/api`` answers are
faked, per scenario. Chrome is driven over the DevTools protocol with the
``websockets`` package the repo already has (no new dependency).

Each scenario runs at a 360 px phone and a 1280 px desktop, in the light
and the dark theme (``domovoi-theme`` in localStorage, which the app turns
into ``<html data-theme>``, plus ``prefers-color-scheme`` emulated to match):

* About & updates on a main-channel house AHEAD of the latest signed
  release (design note UPDATE-NOTICES §7.2 (c)): "128 changes since 1.4.1"
  and "You are ahead of the latest release on unsigned changes." fit their
  card with no horizontal overflow and no page scroll; the "changes since"
  line is ONE line box; no version string is split across lines; the
  "main channel: not a signed release" badge is gone and nothing is said
  twice; a highlight holding ``<b>`` shows it as text. Also with the
  longest version the server lets through (``\\d{1,4}`` × 3).
* Home on a stable house with a notice (§7.1): an admin's row has Update;
  a paired household member's has no button and "An admin can install
  it."; an unpaired browser has no row (and never asks for the notice).

Skipped when no Chrome/Chromium/Edge is found (DOMOVOI_TEST_CHROME names
one). CI's pytest shards set DOMOVOI_REQUIRE_CHROME=1 after checking the
runner's google-chrome (.github/workflows/ci.yml), so there a missing
browser FAILS instead of skipping. No DB, no network: Chrome's host
resolver maps every name but 127.0.0.1 to nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"

WIDTHS = {"phone": 360, "desktop": 1280}
THEMES = ("light", "dark")


# ─── finding a browser ───────────────────────────────────────────────────

def _find_chrome() -> str | None:
    env = os.environ.get("DOMOVOI_TEST_CHROME")
    if env:
        return env if Path(env).exists() or shutil.which(env) else None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        hit = shutil.which(name)
        if hit:
            return hit
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
                p = Path(base) / rel
                if base and p.exists():
                    return str(p)
    if sys.platform == "darwin":
        p = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if p.exists():
            return str(p)
    return None


CHROME = _find_chrome()
REQUIRED = os.environ.get("DOMOVOI_REQUIRE_CHROME") == "1"

if CHROME is None and REQUIRED:
    # CI promised a browser (ci.yml checks google-chrome first): no quiet skip there.
    raise RuntimeError("DOMOVOI_REQUIRE_CHROME=1 but no Chrome/Chromium was found "
                       "(set DOMOVOI_TEST_CHROME to its path)")

pytestmark = [
    pytest.mark.skipif(CHROME is None, reason="no headless Chrome/Chromium/Edge found "
                                              "(set DOMOVOI_TEST_CHROME to its path)"),
    # One browser for the module, on one worker under pytest -n.
    pytest.mark.xdist_group("browser-render"),
]


# ─── the page's server: the real static files, scripted /api answers ────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeApi:
    """The /api answers of the scenario being rendered, and every request seen."""

    def __init__(self) -> None:
        self.table: dict[str, object] = {}
        self.seen: list[str] = []
        self.lock = threading.Lock()

    def set(self, table: dict) -> None:
        with self.lock:
            self.table = dict(table)
            self.seen = []

    def answer(self, method: str, path: str) -> tuple[int, object]:
        with self.lock:
            self.seen.append(f"{method} {path}")
            hit = self.table.get(f"{method} {path}")
        if hit is None:
            return 404, {"detail": "not found"}
        if isinstance(hit, dict) and "__status" in hit:
            return hit["__status"], hit.get("__body")
        return 200, hit


def _app(api: FakeApi) -> FastAPI:
    from web.backend.static_cache import RevalidatingStaticFiles

    app = FastAPI()

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def fake(rest: str, request: Request):
        status, body = api.answer(request.method, f"/api/{rest}")
        return JSONResponse(body, status_code=status)

    @app.websocket("/{rest:path}")
    async def no_stream(ws: WebSocket, rest: str):
        # The state stream isn't part of these scenarios: refused, as a
        # stopped core would; the page keeps its reads.
        await ws.close(code=1008)

    app.mount("/", RevalidatingStaticFiles(directory=str(STATIC), html=True), name="static")
    return app


@pytest.fixture(scope="module")
def site():
    api = FakeApi()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(_app(api), host="127.0.0.1", port=port, lifespan="off",
                                        log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "the static server didn't start"
    yield f"http://127.0.0.1:{port}", api
    srv.should_exit = True
    t.join(timeout=10)


# ─── a DevTools-protocol client (websockets' sync client) ────────────────

class Cdp:
    def __init__(self, url: str) -> None:
        from websockets.sync.client import connect

        self.ws = connect(url, max_size=None, open_timeout=30)
        self.n = 0

    def call(self, method: str, timeout: float = 60, **params) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        end = time.monotonic() + timeout
        while True:
            msg = json.loads(self.ws.recv(timeout=max(0.1, end - time.monotonic())))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str, timeout: float = 60):
        r = self.call("Runtime.evaluate", timeout=timeout, expression=expr, returnByValue=True,
                      awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"page script failed: {r['exceptionDetails']}")
        return r["result"].get("value")

    def close(self) -> None:
        self.ws.close()


@pytest.fixture(scope="module")
def browser():
    profile = tempfile.mkdtemp(prefix="domovoi-render-")
    args = [
        CHROME, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-gpu",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--disable-default-apps", "--disable-domain-reliability", "--no-pings",
        "--metrics-recording-only", "--hide-scrollbars", "--mute-audio",
        # No real network: every name but the test server's address resolves to nothing.
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
        "--window-size=1280,900", "about:blank",
    ]
    if sys.platform.startswith("linux"):
        # CI runners restrict unprivileged user namespaces; /dev/shm is small in containers.
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    env = dict(os.environ)
    if sys.platform == "win32" and os.environ.get("DOMOVOI_TEST_REAL_HOME"):
        # Chrome on Windows refuses remote debugging ("requires a non-default
        # data directory") when USERPROFILE is the suite's stand-in home
        # (home_isolation.py). Its profile is the temp folder above either
        # way; it never touches ~/.domovoi.
        env["USERPROFILE"] = os.environ["DOMOVOI_TEST_REAL_HOME"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        port_file = Path(profile) / "DevToolsActivePort"
        for _ in range(300):
            if port_file.exists() and port_file.read_text().strip():
                break
            if proc.poll() is not None:
                pytest.fail(f"{CHROME} exited with {proc.returncode} before DevTools was up")
            time.sleep(0.1)
        port = int(port_file.read_text().splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
            pages = [t for t in json.load(r) if t.get("type") == "page"]
        assert pages, "Chrome has no page target"
        cdp = Cdp(pages[0]["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Network.setBypassServiceWorker", bypass=True)
        cdp.call("Network.setCacheDisabled", cacheDisabled=False)
        yield cdp
        cdp.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


# The localStorage a load starts from (the theme, a paired browser's token),
# set before any page script runs; cleared for every load so nothing leaks
# between scenarios.
_PRELOAD = """
(() => {
  if (location.origin !== %(origin)s) return;
  try {
    localStorage.clear();
    const ls = %(ls)s;
    for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]);
  } catch (e) {}
})();
"""


def load(cdp: Cdp, base: str, api: FakeApi, *, table: dict, route: str, width: int, theme: str,
         ls: dict | None = None, ready: str) -> None:
    """A fresh load of the dashboard at ``route``, at this width and theme,
    returning once ``ready`` (a JS expression) is true."""
    api.set(table)
    phone = width < 768
    cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=900 if not phone else 780,
             deviceScaleFactor=1, mobile=phone)
    cdp.call("Emulation.setTouchEmulationEnabled", enabled=phone)
    cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": theme}])
    script = cdp.call("Page.addScriptToEvaluateOnNewDocument", source=_PRELOAD % {
        "origin": json.dumps(base), "ls": json.dumps({"domovoi-theme": theme, **(ls or {})})})
    try:
        # A whole new document every time (a hash-only change would not reload).
        cdp.call("Page.navigate", url="about:blank")
        cdp.call("Page.navigate", url=f"{base}/{route}")
        # Babel compiles ~60 files in the page: allow for a slow runner.
        end = time.monotonic() + 120
        last = None
        while time.monotonic() < end:
            try:
                if cdp.js(f"!!({ready})"):
                    break
            except RuntimeError as e:      # mid-navigation
                last = e
            time.sleep(0.25)
        else:
            body = cdp.js("document.body ? document.body.innerText.slice(0, 1500) : ''")
            pytest.fail(f"{route} at {width}px/{theme} never got ready ({ready}); {last}; page: {body!r}")
        # Let late reads land and the layout settle: two animation frames
        # after a quiet half second.
        time.sleep(0.5)
        cdp.js("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        assert cdp.js("document.documentElement.getAttribute('data-theme')") == theme
    finally:
        cdp.call("Page.removeScriptToEvaluateOnNewDocument", identifier=script["identifier"])


# Layout probes, evaluated in the page.
_PROBES = r"""
window.__probe = {
  // How many line boxes an element's text takes (distinct line tops of its
  // text fragments, 2 px apart or more).
  lines(el) {
    const tops = [];
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (!n.data.trim()) continue;
      const r = document.createRange(); r.selectNodeContents(n);
      for (const b of r.getClientRects()) {
        if (b.width < 0.5) continue;
        if (!tops.some((t) => Math.abs(t - b.top) < 2)) tops.push(b.top);
      }
    }
    return tops.length;
  },
  // Every version string in el's text (x.y.z) and every short commit id
  // ("(3a606cd)", a label's tail), and how many line boxes each one takes.
  versions(el) {
    const out = [];
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const re = /\d{1,4}\.\d{1,4}\.\d{1,4}|\([0-9a-f]{7}\)/g;
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      for (const m of n.data.matchAll(re)) {
        const r = document.createRange();
        r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        const tops = [];
        for (const b of r.getClientRects()) {
          if (b.width < 0.5) continue;
          if (!tops.some((t) => Math.abs(t - b.top) < 2)) tops.push(b.top);
        }
        out.push({ text: m[0], lines: tops.length });
      }
    }
    return out;
  },
  box(el) {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
    return { text: el.innerText, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
             height: el.getBoundingClientRect().height, width: el.getBoundingClientRect().width,
             lineHeight: lh, lines: this.lines(el), rects: el.getClientRects().length };
  },
  page() {
    const d = document.documentElement;
    return { scrollWidth: d.scrollWidth, clientWidth: d.clientWidth, innerWidth: window.innerWidth,
             bodyScrollWidth: document.body.scrollWidth };
  },
  // Elements under root wider than root's right edge (the culprits, if any).
  overflowing(root) {
    const right = root.getBoundingClientRect().right + 0.5;
    return [...root.querySelectorAll('*')].filter((e) => {
      const b = e.getBoundingClientRect();
      return b.width > 0 && b.right > right && getComputedStyle(e).position !== 'fixed';
    }).slice(0, 8).map((e) => `${e.tagName.toLowerCase()}.${e.className}: ${(e.innerText || '').slice(0, 60)}`);
  },
  count(text) {
    return document.body.innerText.split(text).length - 1;
  },
};
true
"""


def probe(cdp: Cdp, expr: str):
    return cdp.js(f"(() => {{ {_PROBES}; return ({expr}); }})()")


def assert_no_overflow(m: dict, where: str) -> None:
    for name in ("card", "line", "ahead", "row"):
        b = m.get(name)
        if b is not None:
            assert b["scrollWidth"] <= b["clientWidth"], (where, name, b, m.get("culprits"))
    p = m["page"]
    assert p["scrollWidth"] <= p["clientWidth"], (where, "document scrolls sideways", p, m.get("culprits"))
    assert p["bodyScrollWidth"] <= p["clientWidth"], (where, "body scrolls sideways", p, m.get("culprits"))
    assert not m.get("culprits"), (where, "elements past the card's edge", m["culprits"])


# ─── the fake house ──────────────────────────────────────────────────────

STATUS_ADMIN = {"setup_complete": True, "authenticated": True}
STATUS_GUEST = {"setup_complete": True, "authenticated": False}
PAIRED_LS = {"domovoi-device-token": "house-token", "domovoi-shared-screen": "0"}

BOLD = "<b>Bold</b> stays text: music keeps playing after you ask a question."
HL = ["Friend Houses is open to everyone in the household, by voice too.",
      "Voice ID learns better from short samples, even in a noisy kitchen.", BOLD]


def cfg() -> dict:
    return {"bot_name": "Domovoi", "tts_voice": "v", "rooms": [], "web_version": "1.4.1",
            "wake_word_min_clips": 3, "home_problems_visibility": "everyone",
            "internet_access": "always"}


def updates(*, relation: str, changes: int | None, latest: str, notice: dict | None = None) -> dict:
    return {"auto_check": {"enabled": True, "active": True, "why_off": None,
                           "last_at": None, "last_ok": None},
            "latest_release": {"version": latest, "tag": f"v{latest}", "commit": "a" * 40,
                               "signed": True, "highlights": HL},
            "relation": relation, "changes_since": changes,
            "notice": notice or {"available": False, "updating": False}}


def version(*, channel: str, label: str, upd: dict) -> dict:
    return {"sha": "3a606cd", "running_sha": "3a606cd", "checkout_sha": "3a606cd",
            "version": label, "checkout_version": label, "restart_required": False,
            "restart_capable": True, "restart_mode": "update", "restart_in_progress": False,
            "channel": channel, "channel_intended": channel, "channels": ["stable", "main"],
            "channel_pin": None, "latest_release": upd["latest_release"]["version"],
            "last_update": None, "bad_sha": None, "uptime_sec": 93784,
            "signature": {"status": "unsigned", "enforced": False,
                          "allowed_signers": "/etc/domovoi/allowed_signers"},
            "updates": upd}


def house(**over) -> dict:
    t = {
        "GET /api/auth/status": STATUS_GUEST,
        "GET /api/config": cfg(),
        "GET /api/health": {"status": "ok", "db_reachable": True, "domovoi_reachable": True, "stt": "ok"},
        "GET /api/satellites": [],
        "GET /api/timers": {"server_now": "2026-11-25T10:00:00+00:00", "timers": []},
        "GET /api/plugins": {"plugins": []},
        "GET /api/plugins/manifest": {"plugins": []},
        "GET /api/acquisitions": {"acquisitions": [], "fulfillers": [], "can_fulfill_query": True,
                                  "can_fulfill_url": True, "core_reachable": True},
        "GET /api/calendar/events": [],
        "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [],
        "GET /api/models/hardware": {"disk": {"percent": 40.0}},
        "GET /api/satellites/android-app": {"release": {"version_name": "1.4.1"}, "ready": True,
                                            "fetch": {"running": False}, "auto": {}},
        "GET /api/config/core/restart": {"answering": True},
        "GET /api/internet": {"access": "always"},
    }
    t.update(over)
    return t


# ─── 1. About & updates on a main-channel house, ahead of the release ────

ABOUT_CASES = {
    # A main house 128 merges past 1.4.1 (git_version's label: "1.4.1 + n (sha)").
    "ahead_128": dict(latest="1.4.1", changes=128, label="1.4.1 + 128 (3a606cd)", label_lines=1),
    # The longest version the server lets through (\d{1,4} three times) and
    # a four-digit count: the worst the line can be asked to hold. The
    # running label (31 characters of monospace) may then break at a space
    # on a phone, never inside the version or the commit id.
    "ahead_longest": dict(latest="1234.5678.9012", changes=9999, label="1234.5678.9012 + 9999 (3a606cd)",
                          label_lines=2),
}

_ABOUT_READY = "document.querySelector('.version-main-line')"
_ABOUT_MEASURE = r"""
(() => {
  const P = window.__probe;
  const line = document.querySelector('.version-main-line');
  const card = line.closest('.card');
  const kids = [...line.children];
  const since = kids.find((e) => /change(s)? since /.test(e.innerText));
  const ahead = kids.find((e) => e.innerText.includes('You are ahead of the latest release'));
  const lis = [...line.querySelectorAll('.version-highlights li')];
  return {
    card: P.box(card), line: P.box(line), since: P.box(since), ahead: P.box(ahead),
    page: P.page(), culprits: P.overflowing(card),
    versions: P.versions(card),
    labelLines: document.querySelector('.version-label') ? P.lines(document.querySelector('.version-label')) : null,
    sinceText: since ? since.innerText : null,
    countSince: since ? P.count(since.innerText) : 0,
    countAhead: P.count('You are ahead of the latest release on unsigned changes.'),
    badge: !!document.querySelector('.version-signature'),
    notSigned: P.count('not a signed release'),
    latestWords: P.count('latest release:'),
    highlights: lis.map((li) => ({ text: li.textContent, elements: li.children.length })),
    highlightsTitle: (line.querySelector('.version-highlights-title') || {}).innerText || null,
    theme: document.documentElement.getAttribute('data-theme'),
    fg: getComputedStyle(since || line).color,
    bg: getComputedStyle(card).backgroundColor,
  };
})()
"""

RESULTS: dict[str, dict] = {}


@pytest.mark.parametrize("case", sorted(ABOUT_CASES))
@pytest.mark.parametrize("width", sorted(WIDTHS))
def test_about_main_channel_ahead_fits_on_one_line(browser, site, case, width):
    base, api = site
    c = ABOUT_CASES[case]
    upd = updates(relation="ahead", changes=c["changes"], latest=c["latest"])
    table = house(**{"GET /api/auth/status": STATUS_ADMIN,
                     "GET /api/config/version": version(channel="main", label=c["label"], upd=upd)})
    colours = {}
    for theme in THEMES:
        load(browser, base, api, table=table, route="#settings/about/updates", width=WIDTHS[width],
             theme=theme, ready=_ABOUT_READY)
        m = probe(browser, _ABOUT_MEASURE)
        where = f"{case} {width} {theme}"
        RESULTS[f"about {where}"] = {k: m[k] for k in ("since", "ahead", "card", "page", "labelLines")}
        expected = f"{c['changes']} changes since {c['latest']}"
        assert m["sinceText"] == expected, (where, m["sinceText"])
        # Fits: the line, its two rows and the card, and no sideways page scroll.
        assert_no_overflow(m, where)
        assert m["since"]["scrollWidth"] <= m["since"]["clientWidth"], (where, m["since"])
        assert m["ahead"]["scrollWidth"] <= m["ahead"]["clientWidth"], (where, m["ahead"])
        # "N changes since X" is one line box (and as tall as one line).
        assert m["since"]["lines"] == 1, (where, "the changes line wraps", m["since"])
        assert m["since"]["height"] <= m["since"]["lineHeight"] * 1.5, (where, m["since"])
        # No version string split across lines, the running label included.
        assert m["versions"], where
        split = [v for v in m["versions"] if v["lines"] != 1]
        assert not split, (where, "version split across lines", split)
        assert m["labelLines"] <= c["label_lines"], (where, "the running version label wraps", m["labelLines"])
        # Nothing said twice, and no leftover badge or "latest release" words.
        assert m["countSince"] == 1 and m["countAhead"] == 1, (where, m["countSince"], m["countAhead"])
        assert not m["badge"] and m["notSigned"] == 0, (where, "the not-signed badge is still shown")
        assert m["latestWords"] == 0, where
        # Highlights are text: the <b> shows as written, no element made.
        assert m["highlightsTitle"] and c["latest"] in m["highlightsTitle"], (where, m["highlightsTitle"])
        texts = [h["text"] for h in m["highlights"]]
        assert texts == HL, (where, texts)
        assert all(h["elements"] == 0 for h in m["highlights"]), (where, m["highlights"])
        assert m["theme"] == theme
        colours[theme] = (m["fg"], m["bg"])
    # The themes really differ (the dark palette took).
    assert colours["light"] != colours["dark"], colours


# ─── 2. Home on a stable house with a notice ─────────────────────────────

NOTICE = {"available": True, "version": "1.5.0", "highlights": HL[:2], "found_by": "auto",
          "found_at": "2026-11-25T08:02:11Z", "updating": False}
STABLE_VERSION = version(channel="stable", label="1.4.1",
                         upd=updates(relation="behind", changes=None, latest="1.5.0", notice=NOTICE))

VIEWERS = {
    "admin": dict(status=STATUS_ADMIN, ls=None),
    "member": dict(status=STATUS_GUEST, ls=PAIRED_LS),
    "unpaired": dict(status=STATUS_GUEST, ls=None),
}

# Home is ready once its attention card has settled: the update row (or,
# for a guest, the page's own heading) is drawn and nothing is "checking".
_HOME_READY = ("document.querySelector('.home-att-row, .home-att-quiet, [data-key]')"
               " && !document.body.innerText.includes('checking')")
_HOME_MEASURE = r"""
(() => {
  const P = window.__probe;
  const row = document.querySelector('[data-key="update-available"]');
  const card = row ? row.closest('.card') || row.parentElement : null;
  const buttons = row ? [...row.querySelectorAll('button')].map((b) => b.innerText.trim()) : [];
  const btn = row ? row.querySelector('button') : null;
  const title = row ? row.querySelector('.home-att-update-title') : null;
  return {
    present: !!row, text: row ? row.innerText : null, buttons,
    row: P.box(row), card: P.box(card), title: P.box(title),
    btn: btn ? { width: btn.getBoundingClientRect().width, scrollWidth: btn.scrollWidth,
                 clientWidth: btn.clientWidth, lines: P.lines(btn) } : null,
    versions: row ? P.versions(row) : [],
    page: P.page(), culprits: card ? P.overflowing(card) : [],
    count: P.count('Domovoi 1.5.0 is available'),
    adminLine: P.count('An admin can install it.'),
    homeDrawn: !!document.querySelector('.home-att-row, .home-att-quiet, .home-room, .home-tile'),
    lead: document.body.innerText.slice(0, 200),
    theme: document.documentElement.getAttribute('data-theme'),
  };
})()
"""


@pytest.mark.parametrize("viewer", sorted(VIEWERS))
@pytest.mark.parametrize("width", sorted(WIDTHS))
def test_home_update_row_per_viewer(browser, site, viewer, width):
    base, api = site
    v = VIEWERS[viewer]
    over = {"GET /api/auth/status": v["status"], "GET /api/config/version": STABLE_VERSION,
            "GET /api/updates/notice": NOTICE}
    if viewer == "unpaired":
        # The device tier refuses a guest (it never asks; if it did, no row).
        over["GET /api/updates/notice"] = {"__status": 401, "__body": {"detail": "X-Device-Token required"}}
        over["GET /api/satellites"] = {"__status": 401, "__body": {"detail": "X-Device-Token required"}}
    table = house(**over)
    ready = (_HOME_READY if viewer == "unpaired"
             else "document.querySelector('[data-key=\"update-available\"]')")
    for theme in THEMES:
        load(browser, base, api, table=table, route="#home", width=WIDTHS[width], theme=theme,
             ls=v["ls"], ready=ready)
        m = probe(browser, _HOME_MEASURE)
        where = f"{viewer} {width} {theme}"
        RESULTS[f"home {where}"] = {k: m[k] for k in ("row", "title", "btn", "card", "page", "lead")}
        p = m["page"]
        assert p["scrollWidth"] <= p["clientWidth"], (where, "document scrolls sideways", p)
        assert m["homeDrawn"], (where, "Home didn't render", m["lead"])
        if viewer == "unpaired":
            assert not m["present"] and m["count"] == 0, (where, m["text"])
            assert "GET /api/config" in api.seen, (where, "Home made none of its reads", api.seen)
            assert "GET /api/updates/notice" not in api.seen, (where, "a guest asked for the notice")
            continue
        assert m["present"], where
        assert m["count"] == 1, (where, "said twice")
        assert_no_overflow(m, where)
        assert m["title"]["lines"] == 1, (where, "the row's words wrap", m["title"])
        assert all(x["lines"] == 1 for x in m["versions"]), (where, m["versions"])
        if viewer == "admin":
            assert m["buttons"] == ["Update"], (where, m["buttons"])
            assert m["btn"]["lines"] == 1 and m["btn"]["scrollWidth"] <= m["btn"]["clientWidth"], (where, m["btn"])
            assert m["adminLine"] == 0, where
        else:
            assert m["buttons"] == [], (where, m["buttons"])
            assert m["adminLine"] == 1, where
            assert "GET /api/updates/notice" in api.seen, where


def test_print_measurements():
    """Not a check: the numbers the tests above measured, for the log (-s)."""
    for k, v in RESULTS.items():
        print(k, json.dumps(v, sort_keys=True))
