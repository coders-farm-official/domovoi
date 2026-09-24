"""B1 — Settings → Devices shows the household token to an admin only, and
the server switcher asks before it trusts a newly found server.

Acceptance (security batch B1, FE-2 + the device-tier client work):

2. Settings → Devices shows the household token to an admin session and
   nothing to a cookie-less viewer — and fetches nothing for the viewer,
   so opening the tab signed out never pops the login modal.
3. Selecting a newly discovered server in the switcher shows its address
   and persists it only after the trust confirmation.

The panels are driven for real through domovoi/tests/jsx_interact_harness.js
(the dashboard's own Babel, a small stateful React, a scripted data
layer). No DB, never ``requires_db``; needs ``node`` and fails without it.
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

TOKEN = "hh-0123456789abcdef"
ROTATED = "hh-rotated-fedcba9876543210"
# A token an admin chose: spaces, a comma and punctuation, none of which
# the old lowercase-and-hyphens rule would have allowed through.
CHOSEN = "Maple Street, 1984!"
SET_OK = {"token": CHOSEN, "header": "X-Device-Token", "rotated": True}

SAVE_SCRIPT = (
    """
          h.render();
          await h.click({ type: 'button', text: 'set\u2026' });
          await h.type({ placeholder: 'at least 12 characters' }, '  """
    + CHOSEN
    + """  ');
          await h.click({ type: 'button', text: 'Save token' });
          return { calls: h.calls.map((c) => `${c.method} ${c.path}`),
                   body: h.calls[0] && h.calls[0].body,
                   paired: h.global('window').__paired,
                   dialog: !!h.find({ text: 'Set a custom household token' }) };
    """
)

# The harness's default Auth is a signed-in admin. These `setup` snippets
# replace it for the other cases; they run inside the sandbox before the
# page files load.
VIEWER_SETUP = r"""
Auth = { status: { setup_complete: true, authenticated: false }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status), deviceToken: () => null, pair: () => true };
"""
COOKIE_SETUP = r"""
Auth = { status: { setup_complete: true, authenticated: true }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status), deviceToken: () => null, pair: () => true };
"""
ADMIN_SETUP = r"""
window.__paired = null;
Auth = { status: { setup_complete: true, authenticated: true }, subscribe: () => () => {},
         isLoggedIn: () => true, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status),
         deviceToken: () => window.__paired, pair: (t) => { window.__paired = t; return true; } };
navigator.clipboard = { writeText: (t) => { window.__copied = t; return Promise.resolve(); } };
"""

# A ServerStore stub with the real trust semantics, recording what the
# switcher persisted; `probe` answers for the manual-add path.
SWITCHER_SETUP = r"""
window.__store = { trusted: [], saved: [], selected: null, trustCalls: 0 };
ServerStore = {
  current: () => '',
  currentLabel: () => 'domovoi.lan:6369',
  hostOf: (url) => url.replace(/^https?:\/\//, ''),
  list: () => window.__store.saved.slice(),
  upsert: (url, name) => { window.__store.saved.push({ url, name: name || null }); },
  remove: () => {},
  isTrusted: (url) => !url || window.__store.trusted.includes(url),
  trust: (url) => { window.__store.trusted.push(url); window.__store.trustCalls += 1; },
  untrust: () => {},
  select: (url) => {
    if (url && !window.__store.trusted.includes(url)) return false;
    window.__store.selected = url; return true;
  },
  scanPrefix: () => null,
  probe: (url) => Promise.resolve({ url, name: 'kitchen-box' }),
  scan: () => Promise.resolve([]),
};
"""

SCENARIOS = {
    "admin_sees_the_token": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"}},
        "script": r"""
          h.render();
          const code = h.find({ type: 'code' });
          const copy = h.find({ type: 'button', text: 'copy' });
          await h.click({ type: 'button', text: 'copy' });
          return { token: code && code.text, texts: h.text(), hookCalls: h.hookCalls,
                   copied: h.global('window').__copied, hasCopy: !!copy,
                   hasRotate: !!h.find({ type: 'button', text: 'rotate' }),
                   hasSet: !!h.find({ type: 'button', text: 'set…' }) };
        """,
    },
    "cookie_session_sees_the_token": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": COOKIE_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"}},
        "script": "h.render(); const c = h.find({ type: 'code' }); return { token: c && c.text };",
    },
    "viewer_sees_nothing": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": VIEWER_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"}},
        "script": "h.render(); return { tree: h.tree().length, texts: h.text(), hookCalls: h.hookCalls };",
    },
    # The whole Devices tab, signed out: the token card is absent and the
    # tab never asked for the token.
    "devices_panel_signed_out": {
        "files": [COMPONENTS, SETTINGS], "component": "DevicesPanel",
        "setup": VIEWER_SETUP,
        "api": {"GET /api/devices": [], "GET /api/music/queue-blocks": [], "GET /api/music/now-playing": [],
                "GET /api/files/device-blocks": [], "GET /api/auth/device-token": {"token": TOKEN}},
        "script": r"""
          h.render();
          const cards = h.findAll((el) => el.type === 'div' && (el.props.className || '') === 't').map((d) => d.text);
          return { texts: h.text(), hookCalls: h.hookCalls, cards };
        """,
    },
    "rotate_asks_then_posts_and_re_pairs_this_browser": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token/rotate": {"token": ROTATED, "header": "X-Device-Token", "rotated": True}},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'rotate' });
          const dialog = h.find({ text: 'Rotate the household token?' });
          const afterAsk = { dialog: !!dialog, calls: h.calls.length };
          await h.click({ type: 'button', text: 'Cancel' });
          const afterCancel = { dialog: !!h.find({ text: 'Rotate the household token?' }), calls: h.calls.length };
          await h.click({ type: 'button', text: 'rotate' });
          await h.click({ type: 'button', text: 'Rotate' });
          return { afterAsk, afterCancel, calls: h.calls.map((c) => `${c.method} ${c.path}`),
                   paired: h.global('window').__paired, texts: h.text() };
        """,
    },
    # ── the set dialog ────────────────────────────────────────────────
    "set_asks_first_and_posts_nothing_until_it_is_saved": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": r"""
          h.render();
          const before = h.calls.length;
          await h.click({ type: 'button', text: 'set\u2026' });
          const dialog = h.find({ text: 'Set a custom household token' });
          const afterOpen = { dialog: !!dialog, calls: h.calls.length,
                              counter: h.text().some((t) => t.includes('0 characters.')) };
          await h.click({ type: 'button', text: 'Cancel' });
          const afterCancel = { dialog: !!h.find({ text: 'Set a custom household token' }),
                                calls: h.calls.length };
          return { before, afterOpen, afterCancel };
        """,
    },
    "eleven_characters_leaves_the_save_button_disabled": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'set\u2026' });
          const at = async (v) => {
            await h.type({ placeholder: 'at least 12 characters' }, v);
            const btn = h.find({ type: 'button', text: 'Save token' });
            return { disabled: !!btn.props.disabled,
                     counter: h.text().some((t) => t.includes(v.trim().length + ' characters.')) };
          };
          const eleven = await at('12345678901');
          // Padding does not buy length: the counter counts the STORED form.
          const padded = await at('   12345678901   ');
          const twelve = await at('123456789012');
          return { eleven, padded, twelve, calls: h.calls.length };
        """,
    },
    "saving_posts_the_token_and_re_pairs_this_browser": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": SAVE_SCRIPT,
    },
    "a_server_refusal_lands_inline_next_to_the_field": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token":
                    {"__error": {"status": 400, "message": "at least 12 characters"}}},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'set\u2026' });
          await h.type({ placeholder: 'at least 12 characters' }, 'not-a-good-one');
          await h.click({ type: 'button', text: 'Save token' });
          return { dialog: !!h.find({ text: 'Set a custom household token' }),
                   inline: h.text().some((t) => t.includes('at least 12 characters')),
                   paired: h.global('window').__paired,
                   calls: h.calls.map((c) => `${c.method} ${c.path}`) };
        """,
    },
    "switcher_asks_before_trusting_a_manual_server": {
        "files": [COMPONENTS], "component": "ServerSwitcher", "fnProps": ["onClose"],
        "setup": SWITCHER_SETUP,
        "script": r"""
          h.render();
          await h.type({ placeholder: '192.168.1.30:6369' }, '10.0.0.42');
          await h.click({ type: 'button', text: 'add' });
          const store = () => JSON.parse(JSON.stringify(h.global('window').__store));
          const prompt = h.find({ type: 'div', text: 'trust this server?' });
          const afterPick = { prompt: !!prompt, host: !!h.find({ type: 'div', text: '10.0.0.42:6369' }),
                              name: h.text().some((t) => t.includes('kitchen-box')), store: store() };
          await h.click({ type: 'button', text: 'cancel' });
          const afterCancel = { prompt: !!h.find({ type: 'div', text: 'trust this server?' }), store: store() };
          await h.click({ type: 'button', text: 'add' });
          await h.click({ type: 'button', text: 'trust this server' });
          return { afterPick, afterCancel, afterConfirm: store() };
        """,
    },
    "switcher_uses_a_trusted_server_without_asking": {
        "files": [COMPONENTS], "component": "ServerSwitcher", "fnProps": ["onClose"],
        "setup": SWITCHER_SETUP + "window.__store.trusted.push('http://10.0.0.42:6369');",
        "script": r"""
          h.render();
          await h.type({ placeholder: '192.168.1.30:6369' }, '10.0.0.42');
          await h.click({ type: 'button', text: 'add' });
          const s = h.global('window').__store;
          return { prompt: !!h.find({ type: 'div', text: 'trust this server?' }), selected: s.selected, trustCalls: s.trustCalls };
        """,
    },
}


@pytest.fixture(scope="module")
def outcomes() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive the Settings panels (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


# ── 2. the household token card ─────────────────────────────────────────


def test_admin_session_sees_the_household_token_with_copy_and_rotate(outcomes):
    o = outcomes["admin_sees_the_token"]
    assert o["token"] == TOKEN
    assert "/api/auth/device-token" in o["hookCalls"]
    assert o["hasCopy"] and o["hasRotate"] and o["hasSet"]
    assert o["copied"] == TOKEN
    assert any("Settings → Connection → Household token" in t for t in o["texts"])


def test_cookie_session_sees_the_household_token_too(outcomes):
    assert outcomes["cookie_session_sees_the_token"]["token"] == TOKEN


def test_signed_out_viewer_sees_nothing_and_nothing_is_fetched(outcomes):
    o = outcomes["viewer_sees_nothing"]
    assert o["tree"] == 0
    assert o["texts"] == []
    assert o["hookCalls"] == []


def test_devices_tab_signed_out_has_no_token_card_and_never_asks_for_it(outcomes):
    o = outcomes["devices_panel_signed_out"]
    assert "Household token" not in o["cards"]
    assert "/api/auth/device-token" not in o["hookCalls"]
    assert not any(TOKEN in t for t in o["texts"])
    assert "This device" in o["cards"]              # the rest of the tab is intact


def test_rotate_confirms_first_then_posts_and_re_pairs_this_browser(outcomes):
    o = outcomes["rotate_asks_then_posts_and_re_pairs_this_browser"]
    assert o["afterAsk"] == {"dialog": True, "calls": 0}
    assert o["afterCancel"] == {"dialog": False, "calls": 0}
    assert o["calls"] == ["POST /api/auth/device-token/rotate"]
    assert o["paired"] == ROTATED


# ── the set dialog ──────────────────────────────────────────────────────


def test_set_asks_first_and_posts_nothing_until_it_is_saved(outcomes):
    o = outcomes["set_asks_first_and_posts_nothing_until_it_is_saved"]
    assert o["afterOpen"] == {"dialog": True, "calls": 0, "counter": True}
    assert o["afterCancel"] == {"dialog": False, "calls": 0}


def test_the_twelve_character_floor_is_a_hard_block_on_the_save_button(outcomes):
    o = outcomes["eleven_characters_leaves_the_save_button_disabled"]
    assert o["eleven"] == {"disabled": True, "counter": True}
    # Padding does not buy length — the counter and the gate both measure
    # the STORED form, because that is what the server measures.
    assert o["padded"] == {"disabled": True, "counter": True}
    assert o["twelve"] == {"disabled": False, "counter": True}
    assert o["calls"] == 0


def test_saving_posts_the_trimmed_token_and_re_pairs_this_browser(outcomes):
    o = outcomes["saving_posts_the_token_and_re_pairs_this_browser"]
    assert o["calls"] == ["POST /api/auth/device-token"]
    assert o["body"] == {"token": CHOSEN}        # trimmed, nothing else
    # Re-paired from the RESPONSE, exactly as rotate does, so the card does
    # not turn round and claim this browser is unpaired.
    assert o["paired"] == CHOSEN
    assert o["dialog"] is False


def test_a_server_refusal_lands_inline_and_leaves_the_dialog_open(outcomes):
    """Inline next to the field, not as a toast: the server's message is
    the actionable part and the dialog is still standing."""
    o = outcomes["a_server_refusal_lands_inline_next_to_the_field"]
    assert o["dialog"] is True
    assert o["inline"] is True
    assert o["paired"] is None                    # nothing was re-paired
    assert o["calls"] == ["POST /api/auth/device-token"]


# ── 3. the switcher's trust prompt ──────────────────────────────────────


def test_switcher_shows_the_address_and_persists_only_after_the_confirmation(outcomes):
    o = outcomes["switcher_asks_before_trusting_a_manual_server"]
    assert o["afterPick"]["prompt"] is True
    assert o["afterPick"]["host"] is True             # the address, prominently
    assert o["afterPick"]["name"] is True             # and what it calls itself
    assert o["afterPick"]["store"] == {"trusted": [], "saved": [], "selected": None, "trustCalls": 0}
    assert o["afterCancel"]["prompt"] is False
    assert o["afterCancel"]["store"]["selected"] is None
    assert o["afterCancel"]["store"]["saved"] == []
    assert o["afterConfirm"] == {
        "trusted": ["http://10.0.0.42:6369"],
        "saved": [{"url": "http://10.0.0.42:6369", "name": "kitchen-box"}],
        "selected": "http://10.0.0.42:6369",
        "trustCalls": 1,
    }


def test_switcher_does_not_re_ask_for_a_trusted_server(outcomes):
    o = outcomes["switcher_uses_a_trusted_server_without_asking"]
    assert o == {"prompt": False, "selected": "http://10.0.0.42:6369", "trustCalls": 0}
