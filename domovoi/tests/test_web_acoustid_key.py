"""The AcoustID key field on the dashboard, and the links that lead to it
(2026-10-17).

* Settings → Configuration → Library → "AcoustID key" is write-only: it
  says set / not set (never the key), takes a pasted key, clears only from
  its "clear" button (emptying the box is "no change", never a clear), and
  explains which key: the APPLICATION key from acoustid.org/new-application,
  not the user API key.
* "test the key" asks the server once — about the typed key, else the
  saved one — and says "The key works." or "AcoustID refused this key. Is
  it the application key?". There is no button with nothing to test, and
  none when the internet answer is No: a line points at Settings → Internet
  instead (no dead controls).
* ``#settings/configuration/acoustid-key`` opens Configuration, scrolls to
  the field and puts the cursor in it; the Music page's "song recognition
  needs a free AcoustID key…" and Settings → Internet's song-recognition
  row open that address.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React). No DB, never ``requires_db``; needs
``node`` and fails without it. The server half is
test_acoustid_key_setting.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"
MUSIC = "web/static/music.jsx"

# The address bar, Auth, and a document that records where the page
# scrolled and what it focused (the harness has no DOM).
_SANDBOX = r"""
window.location.hash = __HASH;
window.history = { replaceState: (s, t, url) => { window.location.hash = url; } };
window.__l = [];
window.addEventListener = (t, fn) => { window.__l.push({ t, fn }); };
window.removeEventListener = (t, fn) => { window.__l = window.__l.filter((l) => !(l.t === t && l.fn === fn)); };
window.dispatchEvent = (e) => { window.__l.filter((l) => l.t === e.type).forEach((l) => l.fn(e)); return true; };
window.requestAnimationFrame = (fn) => fn();
CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
window.__dom = { asked: [], scrolled: [], focused: 0 };
document.querySelector = (sel) => {
  window.__dom.asked.push(sel);
  return {
    scrollIntoView: (o) => { window.__dom.scrolled.push(o); },
    querySelector: () => ({ focus: () => { window.__dom.focused += 1; } }),
  };
};
globalThis.Auth = {
  token: 't', status: { setup_complete: true, authenticated: true }, modalOpen: false,
  subscribe: () => () => {},
  refreshStatus: () => Promise.resolve(Auth.status),
  isLoggedIn: () => true, isPaired: () => true, headers: () => ({}),
  openModal: () => {}, logout: () => Promise.resolve(true),
};
globalThis.mutationErrorText = (e, verb) => `${verb} failed: ${(e && e.message) || e}`;
globalThis.useGuestViewer = () => false;
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""


def _setup(hash: str = "#settings/configuration") -> str:
    return f"const __HASH = {json.dumps(hash)};\n" + _SANDBOX


def _row(name, label, group, typ, value, **extra) -> dict:
    row = {"name": name, "label": label, "group": group, "section": "common", "tier": "hot",
           "type": typ, "min": None, "max": None, "choices": None, "unit": None, "help": label,
           "secret": False, "masked": False, "write_only": False, "is_set": None, "value": value,
           "needs_internet": False, "needs_internet_choices": [], "choice_labels": None,
           "internet_profile": False, "follows_internet": False, "set_in_environment": False}
    row.update(extra)
    return row


def _editable(*, is_set: bool, answer: str = "always") -> dict:
    return {"fields": [
        _row("internet_access", "Will this box use the internet?", "Internet", "choice", answer,
             choices=["always", "sometimes", "never"]),
        _row("bot_name", "Bot name", "Identity", "str", "Domovoi"),
        _row("acoustid_api_key", "AcoustID key", "Library", "str", "•••• set" if is_set else "not set",
             secret=True, masked=True, write_only=True, is_set=is_set,
             help="A free APPLICATION key from acoustid.org/new-application"),
        _row("library_enricher_acoustid_min_score", "AcoustID match floor", "Library", "float", 0.7),
    ], "plugin_fields": [], "advanced_available": True}


# What a scenario reads off the AcoustID block.
_READ = r"""
  const w = h.global('window');
  const block = () => h.find((e) => w.__cls(e).includes('config-acoustid'));
  const inBlock = (e) => { const b = block(); return !!b && (e === b || h.ancestors(e).includes(b)); };
  const words = () => { const b = block(); return b ? h.tree().filter((e) => e === b || h.ancestors(e).includes(b)).map((e) => e.text).filter(Boolean).join(' ') : ''; };
  const pill = () => { const r = h.find((e) => e.props && e.props['data-config-field'] === 'acoustid_api_key');
                       const p = r && h.tree().find((e) => w.__cls(e).includes('pill') && h.ancestors(e).includes(r));
                       return p ? w.__deep(p) : null; };
  const testBtn = () => h.findAll({ type: 'button' }).find((e) => inBlock(e) && /test/.test(w.__deep(e))) || null;
  const input = () => h.find((e) => e.type === 'input' && w.__cls(e).includes('config-secret-input'));
  const links = () => h.findAll({ type: 'a' }).filter(inBlock).map((e) => [e.props.href, w.__deep(e)]);
  const result = () => { const r = h.find((e) => w.__cls(e).includes('config-acoustid-result')); return r ? { tone: r.props['data-tone'], text: w.__deep(r) } : null; };
  const save = () => h.findAll({ type: 'button' }).find((e) => w.__deep(e) === 'Save');
  const state = () => ({ pill: pill(), test: testBtn() ? w.__deep(testBtn()) : null, input: input() ? input().props.placeholder : null,
                         result: result(), words: words(), links: links(),
                         saveDisabled: save() ? !!save().props.disabled : null });
"""

SCENARIOS: dict[str, dict] = {
    "unset_type_test_save": {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel",
        "setup": _setup(),
        "api": {"GET /api/config/editable": _editable(is_set=False),
                "POST /api/config/acoustid-test": {"result": "works", "detail": "", "fingerprints": True},
                "PATCH /api/config/editable": {"applied": ["acoustid_api_key"], "restart_required": ["library_enricher_enabled"], "rejected": {}}},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          const first = state();
          await h.type((e) => e === input(), '  Zq7Key42 ');
          const typed = state();
          await h.click((e) => e === testBtn()); await w.__settle(h);
          const tested = state();
          await h.click((e) => e === save()); await w.__settle(h);
          const saved = h.text().join(' ');
          return { first, typed, tested, calls: h.calls, saved };
        """,
    },
    "set_test_refused_then_clear": {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel",
        "setup": _setup(),
        "api": {"GET /api/config/editable": _editable(is_set=True),
                "POST /api/config/acoustid-test": {"result": "refused", "detail": "invalid API key", "fingerprints": True},
                "PATCH /api/config/editable": {"applied": ["acoustid_api_key"], "restart_required": [], "rejected": {}}},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          const first = state();
          await h.click((e) => e === testBtn()); await w.__settle(h);
          const tested = state();
          await h.click((e) => e.type === 'button' && inBlock(e) && w.__deep(e) === 'clear'); await w.__settle(h);
          const clearing = state();
          await h.click((e) => e.type === 'button' && inBlock(e) && w.__deep(e) === 'keep it'); await w.__settle(h);
          const kept = state();
          await h.click((e) => e.type === 'button' && inBlock(e) && w.__deep(e) === 'clear'); await w.__settle(h);
          await h.click((e) => e === save()); await w.__settle(h);
          return { first, tested, clearing, kept, calls: h.calls };
        """,
    },
    "type_then_erase_is_no_change": {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel",
        "setup": _setup(),
        "api": {"GET /api/config/editable": _editable(is_set=True)},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          await h.type((e) => e === input(), 'abc');
          const typed = state();
          await h.type((e) => e === input(), '');
          return { typed, erased: state(), unsaved: h.text().filter((t) => /unsaved|no changes/.test(t)) };
        """,
    },
    "works_but_no_fpcalc": {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel",
        "setup": _setup(),
        "api": {"GET /api/config/editable": _editable(is_set=True),
                "POST /api/config/acoustid-test": {"result": "works", "detail": "", "fingerprints": False}},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          await h.click((e) => e === testBtn()); await w.__settle(h);
          return state();
        """,
    },
    "unreachable_and_internet_off_refusal": {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel",
        "setup": _setup(),
        "api": {"GET /api/config/editable": _editable(is_set=True),
                "POST /api/config/acoustid-test": {"result": "unreachable", "detail": "HTTP request failed", "fingerprints": True}},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          await h.click((e) => e === testBtn()); await w.__settle(h);
          const down = result();
          h.api['POST /api/config/acoustid-test'] = { __error: { status: 409, message: '409 Conflict',
            detail: { detail: 'internet access is turned off for this box (Settings → Internet)' } } };
          await h.click((e) => e === testBtn()); await w.__settle(h);
          return { down, off: result() };
        """,
    },
    "internet_off_no_button": {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel",
        "setup": _setup(),
        "api": {"GET /api/config/editable": _editable(is_set=True, answer="never")},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          await h.type((e) => e === input(), 'Zq7Key42');
          return state();
        """,
    },
    "deep_link": {
        "files": [COMPONENTS, SETTINGS],
        "component": "(window.ACOUSTID_KEY_ROUTE = ACOUSTID_KEY_ROUTE, window.readRouteTab = readRouteTab, SettingsPage)",
        "setup": _setup("#settings/configuration/acoustid-key"),
        "api": {"GET /api/config/editable": _editable(is_set=False)},
        "script": _READ + r"""
          const on = (label) => {
            const b = h.findAll({ type: 'button' }).find((e) => w.__deep(e) === label);
            return b ? String((b.props.style || {}).borderBottom || '').includes('var(--brand)') : null;
          };
          h.render(); await w.__settle(h);
          const lit = String((block().props.style || {}).background);
          const dom = JSON.parse(JSON.stringify(w.__dom));
          // Rendering again (a poll, a save) doesn't scroll again.
          h.rerender(); await w.__settle(h);
          return { config: on('Configuration'), hash: w.location.hash, lit, dom,
                   again: w.__dom.scrolled.length, route: w.ACOUSTID_KEY_ROUTE,
                   read: w.readRouteTab('#settings/configuration/acoustid-key', 'settings', [{ id: 'config', slug: 'configuration' }], 'greetings') };
        """,
    },
    "plain_config_does_not_scroll": {
        "files": [COMPONENTS, SETTINGS], "component": "SettingsPage",
        "setup": _setup("#settings/configuration"),
        "api": {"GET /api/config/editable": _editable(is_set=False)},
        "script": _READ + r"""
          h.render(); await w.__settle(h);
          return { dom: w.__dom, lit: String((block().props.style || {}).background) };
        """,
    },
    "internet_row_links_to_the_field": {
        "files": [COMPONENTS, SETTINGS], "component": "InternetPanel",
        "setup": _setup("#settings/internet"),
        "api": {"GET /api/config/internet": {
            "answer": "always", "answer_locked": False, "answer_source": "env_file", "choices": [],
            "privacy_note": "", "connectivity": {"online": True, "reason": "connected"},
            "features": [
                {"name": "library_enricher_enabled", "label": "Song recognition (AcoustID / Shazam)",
                 "value": False, "next_boot_value": False,
                 "answer_values": {"always": False, "sometimes": False, "never": False},
                 "follows": True, "set_by": "answer", "applies": "restart",
                 "condition": "needs an AcoustID key or the Shazam add-on", "condition_met": False},
                {"name": "news_enabled", "label": "Daily news briefing", "value": True,
                 "next_boot_value": True, "answer_values": {"always": True, "sometimes": True, "never": False},
                 "follows": True, "set_by": "answer", "applies": "restart", "condition": None,
                 "condition_met": None},
            ], "restart_required": [], "search_helper": None, "network_plugins": [], "warnings": []}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return h.findAll({ type: 'a' }).map((e) => [e.props.href, w.__deep(e)]);
        """,
    },
    "music_no_provider": {
        "files": [COMPONENTS, MUSIC], "component": "MusicPage",
        "setup": _setup("#music"),
        "api": {"GET /api/music/now-playing": [], "GET /api/playlists": [],
                "GET /api/music/library/stats": None, "GET /api/acquisitions?limit=100": None,
                "POST /api/music/library/enrich": {"queued": False, "reason": "no_provider"}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          await h.click((e) => e.type === 'button' && w.__deep(e) === 'Enrich tags'); await w.__settle(h);
          const toast = h.find((e) => w.__cls(e).includes('toast-stay'));
          const said = toast ? w.__deep(toast) : null;
          const action = h.findAll({ type: 'button' }).find((e) => w.__deep(e) === 'add the AcoustID key');
          if (action) { await h.click((e) => e === action); await w.__settle(h); }
          return { said, action: !!action, hash: w.location.hash,
                   gone: !h.find((e) => w.__cls(e).includes('toast-stay')) };
        """,
    },
    "music_other_reasons_stay_plain": {
        "files": [COMPONENTS, MUSIC], "component": "MusicPage",
        "setup": _setup("#music"),
        "api": {"GET /api/music/now-playing": [], "GET /api/playlists": [],
                "GET /api/music/library/stats": None, "GET /api/acquisitions?limit=100": None,
                "POST /api/music/library/enrich": {"queued": False, "reason": "disabled"}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          await h.click((e) => e.type === 'button' && w.__deep(e) === 'Enrich tags'); await w.__settle(h);
          return { stay: !!h.find((e) => w.__cls(e).includes('toast-stay')),
                   said: h.text().some((t) => t === 'song recognition is turned off on the server') };
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("acoustid-key") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


KEY_ROUTE = "settings/configuration/acoustid-key"
WHICH_KEY = "https://acoustid.org/new-application"


def test_an_empty_field_says_not_set_and_offers_no_test(driven) -> None:
    first = driven["unset_type_test_save"]["first"]
    assert first["pill"] == "not set"
    assert first["input"] == "paste the key"
    assert first["test"] is None                    # nothing to test yet
    assert [WHICH_KEY, "acoustid.org/new-application"] in first["links"]
    assert "application" in first["words"] and "not the user API key" in first["words"]
    assert "Applies at once" in first["words"]
    assert first["saveDisabled"] is True


def test_a_typed_key_is_tested_then_saved_trimmed(driven) -> None:
    o = driven["unset_type_test_save"]
    assert o["typed"]["test"] == "test the key" and o["typed"]["saveDisabled"] is False
    assert o["tested"]["result"] == {"tone": "ok", "text": "The key works."}
    posts = [c for c in o["calls"] if c["method"] == "POST"]
    assert posts == [{"method": "POST", "path": "/api/config/acoustid-test", "body": {"key": "Zq7Key42"}}]
    patches = [c for c in o["calls"] if c["method"] == "PATCH"]
    assert patches == [{"method": "PATCH", "path": "/api/config/editable",
                        "body": {"changes": {"acoustid_api_key": "  Zq7Key42 "}}}]
    # The restart line names song recognition in words, not its setting name.
    assert "restart the Domovoi server to apply: song recognition" in o["saved"]


def test_a_saved_key_is_tested_as_saved_and_a_refusal_names_the_likely_cause(driven) -> None:
    o = driven["set_test_refused_then_clear"]
    assert o["first"]["pill"] == "set"
    assert o["first"]["input"] == "paste a new key to replace it"
    assert o["first"]["test"] == "test the key"
    assert o["tested"]["result"] == {"tone": "err",
                                     "text": "AcoustID refused this key. Is it the application key?"}
    posts = [c for c in o["calls"] if c["method"] == "POST"]
    assert posts[0]["body"] == {}                   # the saved key: nothing sent from the page


def test_clear_is_a_button_and_can_be_taken_back(driven) -> None:
    o = driven["set_test_refused_then_clear"]
    assert "removed when you save" in o["clearing"]["words"]
    assert o["clearing"]["input"] is None and o["clearing"]["test"] is None   # nothing left to test
    assert o["kept"]["input"] == "paste a new key to replace it"
    assert o["kept"]["saveDisabled"] is True
    patches = [c for c in o["calls"] if c["method"] == "PATCH"]
    assert patches == [{"method": "PATCH", "path": "/api/config/editable",
                        "body": {"changes": {"acoustid_api_key": ""}}}]


def test_emptying_the_box_is_no_change_never_a_clear(driven) -> None:
    o = driven["type_then_erase_is_no_change"]
    assert o["typed"]["saveDisabled"] is False
    assert o["erased"]["saveDisabled"] is True
    assert "removed when you save" not in o["erased"]["words"]
    assert o["unsaved"] == ["no changes"]


def test_a_working_key_on_a_box_without_fpcalc_says_what_is_missing(driven) -> None:
    o = driven["works_but_no_fpcalc"]
    assert o["result"]["tone"] == "ok"
    assert o["result"]["text"].startswith("The key works.")
    assert "can’t fingerprint songs yet" in o["result"]["text"]
    assert ["https://acoustid.org/chromaprint", "Chromaprint’s fpcalc"] in o["links"]


def test_no_answer_is_not_a_refusal(driven) -> None:
    o = driven["unreachable_and_internet_off_refusal"]
    assert o["down"] == {"tone": "warn", "text": "Couldn’t check the key: HTTP request failed."}
    assert o["off"] == {"tone": "err",
                        "text": "internet access is turned off for this box (Settings → Internet)"}


def test_under_no_there_is_no_test_button_but_a_way_to_settings_internet(driven) -> None:
    o = driven["internet_off_no_button"]
    assert o["test"] is None
    assert "Song recognition is off" in o["words"]
    assert ["#settings/internet", "Settings → Internet"] in o["links"]
    assert o["input"] is None or o["input"].startswith("paste")   # the key can still be saved


def test_the_address_opens_the_field(driven) -> None:
    o = driven["deep_link"]
    assert o["route"] == KEY_ROUTE
    assert o["read"] == {"tab": "config", "rest": ["acoustid-key"], "fix": None}
    assert o["config"] is True and o["hash"] == "#settings/configuration/acoustid-key"
    assert o["dom"]["asked"] == ['[data-config-field="acoustid_api_key"]']
    assert o["dom"]["scrolled"] == [{"block": "center"}]
    assert o["dom"]["focused"] == 1
    assert o["lit"] == "var(--brand-soft)"
    assert o["again"] == 1


def test_plain_configuration_neither_scrolls_nor_lights_up(driven) -> None:
    o = driven["plain_config_does_not_scroll"]
    assert o["dom"] == {"asked": [], "scrolled": [], "focused": 0}
    assert o["lit"] == "transparent"


def test_settings_internet_song_recognition_row_links_to_the_field(driven) -> None:
    links = driven["internet_row_links_to_the_field"]
    assert [f"#{KEY_ROUTE}", "needs an AcoustID key or the Shazam add-on"] in links
    assert len([h for h, _ in links if h == f"#{KEY_ROUTE}"]) == 1


def test_the_music_page_message_opens_the_field(driven) -> None:
    o = driven["music_no_provider"]
    assert "song recognition needs a free AcoustID key or the Shazam add-on" in o["said"]
    assert o["action"] is True
    assert o["hash"] == KEY_ROUTE
    assert o["gone"] is True


def test_other_enrich_refusals_stay_plain_toasts(driven) -> None:
    assert driven["music_other_reasons_stay_plain"] == {"stay": False, "said": True}
