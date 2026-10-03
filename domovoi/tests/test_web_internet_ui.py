"""The internet answer in the dashboard, driven outside a browser.

* Settings → Internet (``InternetPanel``): the status line for each state,
  the three choices, the privacy note, Save through the config PATCH (and
  the change announced to the rest of the page), a locked answer, the
  feature chips and "follow the answer again", the restart badge, the
  SearXNG hint and the Microsoft-voices note.
* ``SettingsPage`` opens on the tab ``openSettingsTab`` asked for
  (sessionStorage), and switches when asked while on screen.
* Greyed, never hidden, under ``never``: Configuration's fields that need
  the internet and the Edge choice, the Internet group shown as one line,
  Version's check / pull, the Voices page's Edge controls, Wake Words'
  Train.
* The first-run step after the admin password, its "Decide later", and
  that it is never offered when the answer is already set.
* Home's admin row "tell Domovoi whether this box has internet" while the
  answer is unset, and that it opens Settings → Internet.
* The search helper's state and "start it again", the plugins that may
  reach the internet on their own, the never-only warnings, and the
  internet answer's restart note shown without a technical label.

``jsx_interact_harness.js`` compiles the real components with the
dashboard's own Babel; the Home scenarios reuse ``test_web_home_page``'s
prelude (the real ``auth.js`` + ``data.js`` over a scripted fetch). No DB,
never ``requires_db``; needs ``node``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi import egress
from domovoi.tests import test_web_home_page as home

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"
NEEDS = "needs internet · Settings → Internet"

# ─── the sandbox: sessionStorage, window events, the /api/config read ────

SETUP = r"""
window.__ss = new Map(Object.entries(__SS));
sessionStorage = {
  getItem: (k) => (window.__ss.has(k) ? window.__ss.get(k) : null),
  setItem: (k, v) => { window.__ss.set(k, String(v)); },
  removeItem: (k) => { window.__ss.delete(k); },
};
window.__l = [];
window.__events = [];
window.addEventListener = (t, fn) => { window.__l.push({ t, fn }); };
window.removeEventListener = (t, fn) => { window.__l = window.__l.filter((l) => !(l.t === t && l.fn === fn)); };
window.dispatchEvent = (e) => {
  window.__events.push({ type: e.type, detail: e.detail });
  window.__l.filter((l) => l.t === e.type).forEach((l) => l.fn(e));
  return true;
};
CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
Auth = {
  status: { setup_complete: true, authenticated: true },
  subscribe: () => () => {},
  isLoggedIn: () => true,
  headers: () => ({}),
  modalOpen: false,
  refreshStatus: () => Promise.resolve(Auth.status),
  openModal: () => { window.__signIn = (window.__signIn || 0) + 1; },
  logout: () => Promise.resolve(true),
};
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__byClass = (h, cls) => h.findAll((e) => String(e.props.className || '').split(' ').includes(cls));
window.__radios = (h) => h.findAll((e) => e.type === 'input' && e.props.type === 'radio')
  .map((e) => ({ value: e.props.value, checked: !!e.props.checked, disabled: !!e.props.disabled }));
window.__button = (h, text) => {
  const b = h.find({ type: 'button', text });
  return b ? { disabled: !!b.props.disabled, title: b.props.title || null } : null;
};
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""


def _setup(access: str = "", ss: dict | None = None) -> str:
    return (f"const __ACCESS = {json.dumps(access)}; const __SS = {json.dumps(ss or {})};\n"
            + SETUP)


def feature(name, label, value, nxt, set_by, *, applies="restart", condition=None, met=None,
            values=(True, True, False)) -> dict:
    return {"name": name, "label": label, "value": value, "next_boot_value": nxt,
            "answer_values": dict(zip(("always", "sometimes", "never"), values)),
            "follows": set_by == "answer", "set_by": set_by, "applies": applies,
            "condition": condition, "condition_met": met}


CHOICES = [
    {"value": "always", "label": "Yes, always", "summary": "It's on our home internet.", "detail": "Turns on the extras."},
    {"value": "sometimes", "label": "Sometimes", "summary": "Comes and goes.", "detail": "Small lookups only."},
    {"value": "never", "label": "No, keep everything in the house", "summary": "No internet here.", "detail": "Dark."},
]
PRIVACY = "Your voice and recordings never leave this box, whatever you answer."


def doc(answer="", *, locked=False, reason="connected", features=None, restart=None) -> dict:
    return {
        "answer": answer, "answer_locked": locked,
        "answer_source": "environment" if locked else ("env_file" if answer else "unset"),
        "choices": CHOICES, "privacy_note": PRIVACY,
        "connectivity": {"online": reason == "connected", "reason": reason, "target": "1.1.1.1:443",
                         "last_checked_at": None, "last_online_at": None},
        "hf_hub_offline": False,
        "features": features if features is not None else [
            feature("news_enabled", "Daily news briefing", True, True, "default"),
        ],
        "restart_required": restart or [],
    }


SAVED = {"applied": ["internet_access", "music_alias_fetch_enabled"],
         "restart_required": ["podcast_feed_poller_enabled"], "rejected": {}, "normalized": {},
         "followed": []}

PANEL = "(window.__store = InternetPolicyStore, InternetPanel)"

SCENARIOS: dict[str, dict] = {}

SCENARIOS["panel_unanswered_save"] = {
    "files": [COMPONENTS, SETTINGS], "component": PANEL, "setup": _setup(""),
    "api": {"GET /api/config/internet": doc("", features=[
                feature("podcast_feed_poller_enabled", "Automatic podcast downloads", False, False, "default",
                        values=(True, False, False))]),
            "PATCH /api/config/editable": SAVED},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      const before = { status: w.__byClass(h, 'pill').map((e) => w.__deep(e)),
                       radios: w.__radios(h), save: w.__button(h, 'Save'),
                       privacy: w.__byClass(h, 'internet-privacy').map((e) => w.__deep(e)),
                       edge: w.__byClass(h, 'internet-edge-note').map((e) => w.__deep(e)),
                       hint: w.__byClass(h, 'internet-searxng-hint').length };
      await h.change((e) => e.type === 'input' && e.props.type === 'radio' && e.props.value === 'sometimes', true);
      const picked = { radios: w.__radios(h), save: w.__button(h, 'Save'),
                       hint: w.__byClass(h, 'internet-searxng-hint').map((e) => w.__deep(e)) };
      await h.click({ type: 'button', text: 'Save' });
      await w.__settle(h);
      return { before, picked, calls: h.calls.filter((c) => c.method !== 'GET'), events: w.__events,
               store: w.__store.get(),
               restart: w.__byClass(h, 'internet-restart').map((e) => w.__deep(e)) };
    """,
}

SCENARIOS["panel_locked"] = {
    "files": [COMPONENTS, SETTINGS], "component": PANEL, "setup": _setup("sometimes"),
    "api": {"GET /api/config/internet": doc("sometimes", locked=True)},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      await h.change((e) => e.type === 'input' && e.props.type === 'radio' && e.props.value === 'never', true);
      return { radios: w.__radios(h), save: w.__button(h, 'Save'),
               locked: w.__byClass(h, 'internet-locked').map((e) => w.__deep(e)),
               status: w.__byClass(h, 'internet-status').map((e) => w.__deep(e)) };
    """,
}

SCENARIOS["panel_never_features"] = {
    "files": [COMPONENTS, SETTINGS], "component": PANEL, "setup": _setup("never"),
    "api": {"GET /api/config/internet": doc("never", reason="turned_off", restart=["seed_voice_catalog", "internet_access"], features=[
                feature("news_enabled", "Daily news briefing", True, True, "env_file"),
                feature("music_alias_fetch_enabled", "Find artists by the names people say (MusicBrainz)",
                        False, False, "answer", applies="live"),
                feature("podcast_feed_poller_enabled", "Automatic podcast downloads", False, False,
                        "environment", values=(True, False, False)),
                feature("library_enricher_enabled", "Song recognition (AcoustID / Shazam)", False, False,
                        "answer", condition="needs an AcoustID key or the Shazam add-on", met=False,
                        values=(False, False, False)),
                feature("seed_voice_catalog", "Extra voices at startup", True, False, "answer",
                        values=(False, False, False)),
            ]),
            "PATCH /api/config/editable": {"applied": [], "restart_required": [], "rejected": {},
                                           "normalized": {}, "followed": ["news_enabled"]}},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      const rows = h.findAll((e) => String(e.props.className || '') === 'internet-feature')
        .map((e) => ({ name: e.props['data-feature'], text: w.__deep(e) }));
      const out = { rows, status: w.__byClass(h, 'internet-status').map((e) => w.__deep(e)),
                    hint: w.__byClass(h, 'internet-searxng-hint').length,
                    restart: w.__byClass(h, 'internet-restart').map((e) => w.__deep(e)),
                    follow: h.findAll({ type: 'button', text: 'follow the answer again' }).length };
      await h.click({ type: 'button', text: 'follow the answer again' });
      out.calls = h.calls.filter((c) => c.method !== 'GET');
      return out;
    """,
}

SCENARIOS["panel_signed_out"] = {
    "files": [COMPONENTS, SETTINGS], "component": PANEL, "setup": _setup(""),
    "api": {"GET /api/config/internet": {"__error": {"status": 401, "message": "401 Unauthorized"}}},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      const texts = h.text();
      await h.click({ type: 'button', text: 'sign in' });
      return { texts, signIn: w.__signIn || 0 };
    """,
}

SCENARIOS["settings_deep_link"] = {
    "files": [COMPONENTS, SETTINGS],
    "component": "(window.__tabs = SETTINGS_TABS, window.__open = openSettingsTab, SettingsPage)",
    "setup": _setup("", {"domovoi.settings.tab": "internet"}),
    "api": {"GET /api/config/internet": doc(""), "GET /api/greetings": []},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      const first = { onInternet: !!h.find({ text: 'Will this Domovoi have internet?' }),
                      stored: w.__ss.get('domovoi.settings.tab') || null };
      w.__open('greetings');
      await w.__settle(h);
      const second = { onInternet: !!h.find({ text: 'Will this Domovoi have internet?' }),
                       hash: w.location.hash, stored: w.__ss.get('domovoi.settings.tab') || null };
      return { first, second, tabs: w.__tabs.map((t) => t.id) };
    """,
}

SCENARIOS["settings_default_tab"] = {
    "files": [COMPONENTS, SETTINGS], "component": "SettingsPage",
    "setup": _setup("", {"domovoi.settings.tab": "nonsense"}),
    "api": {"GET /api/greetings": []},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      return { onInternet: !!h.find({ text: 'Will this Domovoi have internet?' }),
               stored: w.__ss.get('domovoi.settings.tab') || null };
    """,
}


def _field(name, label, group, typ, value, **kw) -> dict:
    row = {"name": name, "label": label, "group": group, "section": "common", "tier": "hot",
           "type": typ, "min": None, "max": None, "choices": None, "unit": None, "help": "h",
           "secret": False, "masked": False, "value": value, "choice_labels": None,
           "needs_internet": False, "needs_internet_choices": [], "internet_profile": False,
           "follows_internet": False, "set_in_environment": False}
    row.update(kw)
    return row


def _config_fields(answer: str) -> dict:
    return {"advanced_available": True, "plugin_fields": [], "fields": [
        _field("internet_access", "Will this box use the internet?", "Internet", "choice", answer,
               tier="reapply", choices=["always", "sometimes", "never"],
               choice_labels={"always": "Yes, always", "sometimes": "Sometimes",
                              "never": "No, keep everything in the house"}),
        _field("bot_name", "Bot name", "Identity", "str", "Domovoi"),
        _field("tts_engine", "TTS engine", "Voice & speech", "choice", "piper",
               choices=["piper", "edge", "system"], needs_internet_choices=["edge"]),
        _field("music_alias_fetch_enabled", "Look up other names on MusicBrainz", "Library", "bool",
               answer == "always", needs_internet=True, internet_profile=True,
               follows_internet=bool(answer)),
        _field("news_auto_fetch", "Auto-fetch topic news", "News", "bool", False, needs_internet=True),
    ]}


VERSION = {"sha": "abc1234", "checkout_sha": "abc1234", "restart_required": False,
           "restart_capable": True, "restart_mode": "restart", "last_update": None, "bad_sha": None,
           "plugins_pending_restart": []}

CONFIG_SCRIPT = r"""
  const w = h.global('window');
  h.render(); await w.__settle(h);
  const inputs = h.findAll((e) => e.type === 'input' || e.type === 'select')
    .map((e) => ({ type: e.type, kind: e.props.type || null, disabled: !!e.props.disabled,
                   title: e.props.title || null, value: e.props.value === undefined ? null : e.props.value,
                   checked: e.props.checked === undefined ? null : !!e.props.checked }));
  const options = h.findAll({ type: 'option' }).map((e) => ({ value: e.props.value, text: e.text,
                                                              disabled: !!e.props.disabled }));
  return { inputs, options, line: w.__byClass(h, 'config-internet-line').map((e) => w.__deep(e)),
           follows: w.__byClass(h, 'config-follows-internet').length,
           notes: w.__byClass(h, 'needs-internet').length,
           labels: h.text(),
           check: w.__button(h, 'Check for updates'), calls: h.calls.map((c) => `${c.method} ${c.path}`) };
"""

for answer in ("never", "always", ""):
    SCENARIOS[f"config_{answer or 'unset'}"] = {
        "files": [COMPONENTS, SETTINGS], "component": "ConfigPanel", "setup": _setup(answer),
        "api": {"GET /api/config/editable": _config_fields(answer),
                "GET /api/config": {"web_version": "w", "internet_access": answer},
                "GET /api/config/version": VERSION},
        "script": CONFIG_SCRIPT,
    }

EDGE = {"id": 1, "name": "Aria", "engine": "edge", "model_ref": "en-US-AriaNeural", "is_default": False}
PIPER = {"id": 2, "name": "Amy", "engine": "piper", "model_ref": "amy.onnx", "is_default": True}
PIPER2 = {"id": 3, "name": "Ryan", "engine": "piper", "model_ref": "ryan.onnx", "is_default": False}

VOICES_SCRIPT = r"""
  const w = h.global('window');
  h.render(); await w.__settle(h);
  const plays = h.findAll((e) => e.type === 'button' && String(e.props.className).includes('btn-icon')
                                 && (e.props.title === 'Play a sample' || e.props.title === __NEEDS))
    .map((e) => ({ disabled: !!e.props.disabled, title: e.props.title }));
  const makeDefault = h.findAll({ type: 'button', text: 'make default' })
    .map((e) => ({ disabled: !!e.props.disabled, title: e.props.title || null }));
  const textInputs = h.findAll((e) => e.type === 'input' && !e.props.type).map((e) => !!e.props.disabled);
  return { register: w.__button(h, 'Register'), upload: w.__button(h, 'Upload voice'),
           plays, makeDefault, textInputs, notes: w.__byClass(h, 'needs-internet').length };
"""

for answer in ("never", "always"):
    SCENARIOS[f"voices_{answer}"] = {
        "files": [COMPONENTS, SETTINGS], "component": "VoicesPanel",
        "setup": _setup(answer),
        "api": {"GET /api/voices": [EDGE, PIPER, PIPER2],
                "GET /api/config": {"bot_name": "x", "internet_access": answer}},
        "script": f"const __NEEDS = {json.dumps(NEEDS)};" + VOICES_SCRIPT,
    }

WAKE = {"id": 7, "name": "Hey Domovoi", "phrase": "hey domovoi", "status": "recording",
        "threshold": 0.5, "is_default": False, "clip_count": 20, "selected_count": 20}

for answer in ("never", "always"):
    SCENARIOS[f"wake_{answer}"] = {
        "files": [COMPONENTS, SETTINGS], "component": "WakeWordsPanel", "setup": _setup(answer),
        "api": {"GET /api/wake-words": [WAKE], "GET /api/satellites": [],
                "GET /api/config": {"wake_word_min_clips": 3, "internet_access": answer}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return { train: w.__button(h, 'Train'), notes: w.__byClass(h, 'needs-internet').length };
        """,
    }

# ─── the shared policy hook ──────────────────────────────────────────────

POLICY_PROBE = (
    "(window.__store = InternetPolicyStore, window.__announce = announceInternetChange, (() => {"
    " function PolicyRow() { const p = useInternetPolicy();"
    "   return React.createElement('div', { 'data-probe': JSON.stringify({ access: p.access, off: p.off,"
    "     unanswered: p.unanswered, loaded: p.loaded }) }); }"
    " return function PolicyProbe() { return React.createElement('div', null,"
    "   React.createElement(PolicyRow, { key: 'a' }), React.createElement(PolicyRow, { key: 'b' })); };"
    "})())"
)

POLICY_SCRIPT = r"""
  const w = h.global('window');
  const read = () => h.findAll((e) => e.props && e.props['data-probe']).map((e) => JSON.parse(e.props['data-probe']));
  h.render();
  const initial = read();
  await w.__settle(h);
  const loaded = read();
  const readsAfterLoad = h.calls.map((c) => `${c.method} ${c.path}`);
  w.__announce('always');
  h.rerender();
  const announced = read();
  await w.__settle(h);
  return { initial, loaded, readsAfterLoad, announced, events: w.__events,
           reads: h.calls.map((c) => `${c.method} ${c.path}`) };
"""

for name, cfg in (("policy_never", {"internet_access": "never"}),
                  ("policy_unset", {"internet_access": ""}),
                  ("policy_old_server", {"bot_name": "x"})):
    SCENARIOS[name] = {"files": [COMPONENTS], "component": POLICY_PROBE, "setup": _setup(),
                       "api": {"GET /api/config": cfg}, "script": POLICY_SCRIPT}


# ─── first-run setup: the third step ─────────────────────────────────────

FIRST_RUN_AUTH = r"""
window.__auth = { open: true, setup: null, login: null, closes: 0 };
Auth = {
  status: { setup_complete: __SETUP_DONE, authenticated: false },
  subscribe: () => () => {},
  isLoggedIn: () => false,
  headers: () => ({}),
  get modalOpen() { return window.__auth.open; },
  get pairModalOpen() { return false; },
  refreshStatus: () => Promise.resolve(Auth.status),
  closeModal: () => { window.__auth.open = false; window.__auth.closes += 1; },
  closePairModal: () => {},
  setup: (code, pw) => { window.__auth.setup = [code, pw]; window.__auth.open = false;
                         return Promise.resolve({ ok: true, token: 'bearer-1' }); },
  login: (pw) => { window.__auth.login = pw; window.__auth.open = false;
                   return Promise.resolve({ ok: true, token: 'bearer-1' }); },
};
"""

FIRST_RUN_SCRIPT = r"""
  const w = h.global('window');
  h.render(); await w.__settle(h);
  if (__SETUP_DONE) {
    await h.type({ type: 'input' }, 'correct horse battery');
    await h.click({ type: 'button', text: 'log in' });
  } else {
    await h.type({ type: 'input', nth: 0 }, 'one-two-three-four-five-six-seven-eight');
    await h.type({ type: 'input', nth: 1 }, 'correct horse battery');
    await h.type({ type: 'input', nth: 2 }, 'correct horse battery');
    await h.click({ type: 'button', text: 'set password' });
  }
  await w.__settle(h, 6);
  const titles = () => h.findAll((e) => String(e.props.className || '') === 'ttl').map((e) => e.text);
  const step = { titles: titles(), radios: w.__radios(h), save: w.__button(h, 'Save'),
                 later: !!h.find({ type: 'button', text: 'Decide later' }),
                 privacy: h.text().some((t) => t.includes('never leave this box')) };
  if (__ACTION === 'save') {
    await h.change((e) => e.type === 'input' && e.props.type === 'radio' && e.props.value === 'always', true);
    await h.click({ type: 'button', text: 'Save' });
  } else if (__ACTION === 'later') {
    await h.click({ type: 'button', text: 'Decide later' });
  }
  await w.__settle(h);
  return { step, after: titles(), calls: h.calls.map((c) => ({ m: c.method, p: c.path, b: c.body })),
           auth: w.__auth, store: w.__store.get(), events: w.__events };
"""


def _first_run(action: str, *, answer: str = "", setup_done: bool = False) -> dict:
    head = (f"const __SETUP_DONE = {json.dumps(setup_done)}; const __ACTION = {json.dumps(action)};\n")
    return {
        "files": [COMPONENTS], "component": "(window.__store = InternetPolicyStore, AuthModalHost)",
        "setup": _setup(answer) + head + FIRST_RUN_AUTH,
        "api": {"GET /api/config": {"bot_name": "x", "internet_access": answer},
                "PATCH /api/config/editable": {"applied": ["internet_access"], "restart_required": [],
                                               "rejected": {}, "normalized": {}, "followed": []}},
        "script": head + FIRST_RUN_SCRIPT,
    }


SCENARIOS["first_run_save"] = _first_run("save")
SCENARIOS["first_run_later"] = _first_run("later")
SCENARIOS["first_run_already_answered"] = _first_run("none", answer="never")
SCENARIOS["login_never_asks"] = _first_run("none", setup_done=True)


HELPER_FAILED = {"state": "failed", "detail": "Error response from daemon: pull access denied",
                 "at": "2026-10-03T10:00:00+00:00", "managed": True}
NOTE = "the speech models switch their download checks after a restart"


def _doc_plus(answer, **extra) -> dict:
    d = doc(answer, reason="turned_off" if answer == "never" else "connected")
    d.update(extra)
    return d


SCENARIOS["panel_helper_and_plugins"] = {
    "files": [COMPONENTS, SETTINGS], "component": PANEL, "setup": _setup("always"),
    "api": {"GET /api/config/internet": _doc_plus(
                "always", search_helper=HELPER_FAILED, warnings=[],
                network_plugins=[{"slug": "kiwix", "name": "Kiwix", "bundled": False},
                                 {"slug": "radio", "name": "Radio", "bundled": True}]),
            "POST /api/config/internet/search-helper": {"scheduled": True,
                                                        "search_helper": dict(HELPER_FAILED, state="starting")}},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      const out = { helper: w.__byClass(h, 'internet-search-helper').map((e) => w.__deep(e)),
                    plugins: w.__byClass(h, 'internet-network-plugins').map((e) => w.__deep(e)),
                    warnings: w.__byClass(h, 'internet-warnings').length,
                    again: w.__button(h, 'start it again') };
      await h.click({ type: 'button', text: 'start it again' });
      await w.__settle(h);
      out.calls = h.calls.filter((c) => c.method !== 'GET').map((c) => ({ m: c.method, p: c.path, b: c.body }));
      return out;
    """,
}

SCENARIOS["panel_never_warnings_and_note"] = {
    "files": [COMPONENTS, SETTINGS], "component": PANEL, "setup": _setup("sometimes"),
    "api": {"GET /api/config/internet": _doc_plus(
                "sometimes", search_helper={"state": "running", "detail": "", "at": None, "managed": True},
                network_plugins=[{"slug": "radio", "name": "Radio", "bundled": True}],
                warnings=["The language model server (https://ollama.example.com) is not on this network."]),
            "PATCH /api/config/editable": {"applied": ["internet_access"],
                                           "restart_required": ["news_enabled", "internet_access"],
                                           "rejected": {}, "normalized": {"internet_access": NOTE},
                                           "followed": []}},
    "script": r"""
      const w = h.global('window');
      h.render(); await w.__settle(h);
      const before = { helper: w.__byClass(h, 'internet-search-helper').map((e) => w.__deep(e)),
                       again: w.__button(h, 'start it again'),
                       plugins: w.__byClass(h, 'internet-network-plugins').length,
                       warnings: w.__byClass(h, 'internet-warnings').map((e) => w.__deep(e)) };
      await h.change((e) => e.type === 'input' && e.props.type === 'radio' && e.props.value === 'never', true);
      await h.click({ type: 'button', text: 'Save' });
      await w.__settle(h);
      return { before, texts: h.text(), restart: w.__byClass(h, 'internet-restart').map((e) => w.__deep(e)) };
    """,
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("internet-ui") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


# ─── Settings → Internet ──────────────────────────────────────────────────


def test_an_unanswered_box_says_so_and_offers_the_three_answers(driven) -> None:
    b = driven["panel_unanswered_save"]["before"]
    assert "Not answered yet: Domovoi behaves as it always has" in b["status"]
    assert [r["value"] for r in b["radios"]] == ["always", "sometimes", "never"]
    assert not any(r["checked"] for r in b["radios"])
    assert b["save"]["disabled"] is True                       # nothing picked yet
    assert b["privacy"] == [PRIVACY]
    assert b["edge"] == ["Microsoft voices stay your choice on every answer (Settings → Voices)."]
    assert b["hint"] == 0


def test_saving_an_answer_patches_it_and_tells_the_page(driven) -> None:
    o = driven["panel_unanswered_save"]
    assert o["picked"]["save"]["disabled"] is False
    assert [r["value"] for r in o["picked"]["radios"] if r["checked"]] == ["sometimes"]
    assert o["picked"]["hint"] == ["Web answers use the search helper (SearXNG). Domovoi starts it "
                                   "when you save Yes or Sometimes (the first time it downloads a few "
                                   "hundred MB); on a Linux appliance an update also starts it."]
    assert o["calls"] == [{"method": "PATCH", "path": "/api/config/editable",
                           "body": {"changes": {"internet_access": "sometimes"}}}]
    assert o["events"] == [{"type": "domovoi:internet-changed", "detail": {"access": "sometimes"}}]
    assert o["store"] == {"access": "sometimes", "loaded": True}
    assert any("saved — restart the Domovoi server to apply: Automatic podcast downloads" in t
               for t in o["restart"])


def test_an_answer_pinned_in_the_environment_is_read_only(driven) -> None:
    o = driven["panel_locked"]
    assert all(r["disabled"] for r in o["radios"])
    assert [r["value"] for r in o["radios"] if r["checked"]] == ["sometimes"]   # the change didn't take
    assert o["save"]["disabled"] is True
    assert "set in the server's environment (INTERNET_ACCESS)" in o["locked"][0]
    assert "Connected" in o["status"][0]


def test_the_feature_chips_and_follow_again(driven) -> None:
    o = driven["panel_never_features"]
    assert "Turned off for this box" in o["status"][0]
    assert o["hint"] == 0                                           # no SearXNG hint under No
    rows = {r["name"]: r["text"] for r in o["rows"]}
    assert "set in domovoi/.env" in rows["news_enabled"] and "follow the answer again" in rows["news_enabled"]
    assert "follows the answer" in rows["music_alias_fetch_enabled"]
    assert "set in the server's environment" in rows["podcast_feed_poller_enabled"]
    assert "needs an AcoustID key or the Shazam add-on" in rows["library_enricher_enabled"]
    assert "→ off after a restart" in rows["seed_voice_catalog"]
    assert "Yes: off · Sometimes: off · No: off" in rows["seed_voice_catalog"]
    assert o["follow"] == 1
    assert o["calls"] == [{"method": "PATCH", "path": "/api/config/editable",
                           "body": {"changes": {}, "follow_internet": ["news_enabled"]}}]
    assert "Extra voices at startup, the speech models\u2019 download checks" in o["restart"][0]
    assert "Hugging Face" not in o["restart"][0]


def test_the_search_helper_state_and_start_it_again(driven) -> None:
    o = driven["panel_helper_and_plugins"]
    assert len(o["helper"]) == 1
    assert "couldn\u2019t start" in o["helper"][0] and "pull access denied" in o["helper"][0]
    assert o["again"] == {"disabled": False, "title": "runs the same start Domovoi does when you save Yes or Sometimes"}
    assert o["calls"] == [{"m": "POST", "p": "/api/config/internet/search-helper", "b": {}}]


def test_plugins_that_may_reach_the_internet_are_listed(driven) -> None:
    o = driven["panel_helper_and_plugins"]
    assert len(o["plugins"]) == 1
    assert "Kiwix" in o["plugins"][0] and "Radio" not in o["plugins"][0]   # the bundled one follows
    assert o["warnings"] == 0


def test_a_running_helper_offers_no_button_and_never_shows_its_warnings(driven) -> None:
    o = driven["panel_never_warnings_and_note"]
    assert o["before"]["again"] is None
    assert "running" in o["before"]["helper"][0]
    assert o["before"]["plugins"] == 0                    # only the bundled plugin
    assert o["before"]["warnings"] == ["The language model server (https://ollama.example.com) is not on this network."]


def test_the_internet_restart_note_has_no_technical_label(driven) -> None:
    o = driven["panel_never_warnings_and_note"]
    assert NOTE in o["texts"]                             # the note on its own
    assert not any("Hugging Face" in t for t in o["texts"])
    assert any("the speech models\u2019 download checks" in t for t in o["restart"])


def test_a_signed_out_read_offers_the_sign_in(driven) -> None:
    o = driven["panel_signed_out"]
    assert any("admin login required" in t for t in o["texts"])
    assert o["signIn"] == 1


def test_settings_opens_on_the_tab_it_was_sent_to(driven) -> None:
    o = driven["settings_deep_link"]
    assert o["first"] == {"onInternet": True, "stored": None}       # read once, then cleared
    assert o["second"]["onInternet"] is False                       # switched while on screen
    assert o["second"]["hash"] == "settings"
    tabs = o["tabs"]
    assert "internet" in tabs and tabs.index("internet") == tabs.index("config") - 1
    assert driven["settings_default_tab"] == {"onInternet": False, "stored": None}


# ─── Greyed, not hidden ───────────────────────────────────────────────────


def _inputs(o: dict) -> list[dict]:
    return o["inputs"]


def test_configuration_greys_what_needs_the_internet_under_never(driven) -> None:
    o = driven["config_never"]
    boxes = [i for i in o["inputs"] if i["kind"] == "checkbox"]
    assert boxes and all(i["disabled"] and i["title"] == NEEDS for i in boxes)   # alias fetch + topic news
    text_inputs = [i for i in o["inputs"] if i["type"] == "input" and i["kind"] == "text"]
    assert text_inputs and not any(i["disabled"] for i in text_inputs)           # bot name stays
    edge = next(x for x in o["options"] if x["value"] == "edge")
    assert edge["disabled"] is True and edge["text"] == "edge · needs internet"
    assert next(x for x in o["options"] if x["value"] == "piper")["disabled"] is False
    assert o["notes"] >= 2
    assert o["line"] == ["Internet: No, keep everything in the house · change it in Settings → Internet"]
    assert "Will this box use the internet?" not in o["labels"]                  # one line, not a field
    assert o["follows"] == 1
    assert o["check"] == {"disabled": True, "title": NEEDS}                      # Version: check greyed
    assert o["calls"] == []                     # greyed from the reads the page makes anyway


def test_configuration_greys_nothing_when_the_internet_is_allowed(driven) -> None:
    o = driven["config_always"]
    assert not any(i["disabled"] for i in o["inputs"])
    assert not any(x["disabled"] for x in o["options"])
    assert o["notes"] == 0
    assert o["line"] == ["Internet: Yes, always · change it in Settings → Internet"]
    assert o["check"] == {"disabled": False, "title": None}
    unset = driven["config_unset"]
    assert unset["line"] == ["Internet: not answered · change it in Settings → Internet"]
    assert unset["follows"] == 0


def test_voices_grey_the_microsoft_controls_under_never(driven) -> None:
    o = driven["voices_never"]
    assert o["register"] == {"disabled": True, "title": NEEDS}
    assert o["upload"]["disabled"] is False                                      # local voices stay
    assert all(o["textInputs"][:2])                                              # the Edge name + id
    assert {"disabled": True, "title": NEEDS} in o["plays"]                      # Aria's sample
    assert {"disabled": False, "title": "Play a sample"} in o["plays"]           # Amy / Ryan
    assert o["makeDefault"] == [{"disabled": True, "title": NEEDS},              # Aria
                                {"disabled": False, "title": None}]              # Ryan
    a = driven["voices_always"]
    assert a["register"] == {"disabled": False, "title": None}
    assert not any(p["disabled"] for p in a["plays"])
    assert a["notes"] == 0


def test_wake_word_training_is_greyed_under_never(driven) -> None:
    assert driven["wake_never"]["train"] == {"disabled": True, "title": NEEDS}
    assert driven["wake_never"]["notes"] == 1
    assert driven["wake_always"]["train"] == {"disabled": False, "title": None}


# ─── First-run setup ──────────────────────────────────────────────────────


def test_first_run_asks_the_internet_question_after_the_password(driven) -> None:
    o = driven["first_run_save"]
    assert o["auth"]["setup"] == ["one-two-three-four-five-six-seven-eight", "correct horse battery"]
    assert o["step"]["titles"] == ["will this Domovoi have internet?"]
    assert [r["value"] for r in o["step"]["radios"]] == ["always", "sometimes", "never"]
    assert o["step"]["later"] is True and o["step"]["privacy"] is True
    assert o["step"]["save"]["disabled"] is True
    patches = [c for c in o["calls"] if c["m"] == "PATCH"]
    assert patches == [{"m": "PATCH", "p": "/api/config/editable",
                        "b": {"changes": {"internet_access": "always"}}}]
    assert o["after"] == []                                         # closed after the save
    assert o["store"] == {"access": "always", "loaded": True}


def test_decide_later_closes_without_saving(driven) -> None:
    o = driven["first_run_later"]
    assert o["step"]["titles"] == ["will this Domovoi have internet?"]
    assert not [c for c in o["calls"] if c["m"] == "PATCH"]
    assert o["after"] == []


def test_the_step_is_not_offered_when_already_answered_or_on_a_plain_login(driven) -> None:
    o = driven["first_run_already_answered"]
    assert o["step"]["titles"] == []
    assert [c["p"] for c in o["calls"]] == ["/api/config"]          # it looked, and kept quiet
    login = driven["login_never_asks"]
    assert login["auth"]["login"] == "correct horse battery"
    assert login["step"]["titles"] == []
    assert login["calls"] == []


# ─── Home ─────────────────────────────────────────────────────────────────

HOME_EXTRA = r"""
window.__ss = new Map();
sessionStorage = {
  getItem: (k) => (window.__ss.has(k) ? window.__ss.get(k) : null),
  setItem: (k, v) => { window.__ss.set(k, String(v)); },
  removeItem: (k) => { window.__ss.delete(k); },
};
"""

HOME_SCRIPT = (
    "const before = w.__snap(h);"
    "const row = h.find((e) => e.props && e.props['data-key'] === 'internet');"
    "if (row) await h.click((e) => e.props && e.props['data-key'] === 'internet');"
    "return { before, hash: w.location.hash, tab: w.__ss.get('domovoi.settings.tab') || null };"
)


def _home(cfg_extra: dict, *, admin: bool) -> dict:
    table = home.house(**{
        "GET /api/config": {**home.cfg(), **cfg_extra},
        **({"GET /api/auth/status": home.STATUS_ADMIN} if admin else {}),
    })
    sc = home.scenario(table, HOME_SCRIPT, ls=home.PAIRED_LS)
    sc["setup"] = sc["setup"] + HOME_EXTRA
    return sc


HOME_SCENARIOS = {
    "admin_unanswered": _home({"internet_access": ""}, admin=True),
    "admin_answered": _home({"internet_access": "always"}, admin=True),
    "admin_old_server": _home({}, admin=True),
    "household_unanswered": _home({"internet_access": ""}, admin=False),
}


@pytest.fixture(scope="module")
def home_driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("internet-home") / "scenarios.json"
    spec.write_text(json.dumps(HOME_SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(home.HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _internet_rows(o: dict) -> list[dict]:
    return [r for r in o["before"]["attention"] if r["key"] == "internet"]


def test_home_asks_an_admin_while_the_answer_is_unset(home_driven) -> None:
    o = home_driven["admin_unanswered"]
    rows = _internet_rows(o)
    assert len(rows) == 1
    assert rows[0]["text"] == "tell Domovoi whether this box has internet"   # lower case, like its siblings
    assert rows[0]["href"] == "#settings"
    # clicking it opens Settings on the Internet tab
    assert o["hash"] == "settings" and o["tab"] == "internet"


def test_home_does_not_ask_when_answered_or_on_an_older_server(home_driven) -> None:
    assert _internet_rows(home_driven["admin_answered"]) == []
    assert _internet_rows(home_driven["admin_old_server"]) == []


def test_home_never_shows_the_row_to_a_household_member(home_driven) -> None:
    assert _internet_rows(home_driven["household_unanswered"]) == []


def test_the_dashboard_and_the_server_say_the_same_refusal() -> None:
    src = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    assert f"const INTERNET_OFF_MESSAGE = '{egress.TURNED_OFF_REASON}';" in src
    assert f"const NEEDS_INTERNET_TEXT = '{NEEDS}';" in src


# ─── The shared hook ──────────────────────────────────────────────────────


def test_use_internet_policy_is_one_shared_read(driven) -> None:
    o = driven["policy_never"]
    assert o["initial"] == [{"access": "", "off": False, "unanswered": False, "loaded": False}] * 2
    assert o["loaded"] == [{"access": "never", "off": True, "unanswered": False, "loaded": True}] * 2
    assert o["readsAfterLoad"] == ["GET /api/config"]                # two users, one read
    # a save elsewhere on the page: every user hears it at once, and re-reads
    assert o["announced"] == [{"access": "always", "off": False, "unanswered": False, "loaded": True}] * 2
    assert o["events"] == [{"type": "domovoi:internet-changed", "detail": {"access": "always"}}]
    assert o["reads"] == ["GET /api/config", "GET /api/config"]


def test_use_internet_policy_unanswered_and_older_servers(driven) -> None:
    assert driven["policy_unset"]["loaded"] == [
        {"access": "", "off": False, "unanswered": True, "loaded": True}] * 2
    assert driven["policy_old_server"]["loaded"] == [
        {"access": "", "off": False, "unanswered": True, "loaded": True}] * 2
